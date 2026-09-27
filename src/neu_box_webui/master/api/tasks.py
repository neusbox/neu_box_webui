"""Master 任务入口 — 提交/查看/删除，转发到对应 Worker。

权限模型（服务端强制，前端只负责隐藏入口）:
  - 提交:  user_id 一律由服务端从 session 注入（忽略前端传值），
           并写入 task_submissions 登记表
  - 查看:  主队列与任务元数据全员可见
  - 日志:  仅任务本人或 admin（NEU_BOX_LOGS_SHARED=1 时放开）
  - 删除:  仅任务本人或 admin（逐条校验）
"""

import logging
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, request

from neu_box_webui.config import env_text
from neu_box_webui.master.api.auth import get_current_user, login_required
from neu_box_webui.master.services.db import Database
from neu_box_webui.master.services.nodes_pool import Nodes_Pool

tasks_bp = Blueprint('tasks', __name__)
logger = logging.getLogger('master.tasks')


def __getattr__(name: str):
    """延迟获取 Database 单例，避免导入期绑定导致测试/迁移后拿到旧实例。"""
    if name == 'db':
        return Database.get_instance()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

def _db() -> "Database":
    """每次调用取当前单例（延迟绑定，便于测试替换/迁移）。"""
    return Database.get_instance()


# 实验室场景下可放开日志互相查看
_LOGS_SHARED = env_text('NEU_BOX_LOGS_SHARED', '0') in ('1', 'true', 'yes')


def _current_username() -> str:
    user = get_current_user()
    return user['username'] if user else ''


def _node_name(node_id: str) -> str | None:
    """node_id → 节点名（凭据按节点名存储）。"""
    try:
        node = Nodes_Pool.get_nodes_pool().get_node_by_id(node_id)
    except Exception:
        return None
    return node.name if node else None


def _labels_for(user: dict | None, node_name: str | None) -> set[str]:
    """某用户在该节点上的任务 user_id 归属集合。

    = {WebUI 用户名} ∪ {节点凭据用户名（如已设置）}。
    提交任务时用凭据用户名作 worker 侧 user_id（未设置时用 WebUI
    用户名），所以两个标签都可能出现在历史任务上。
    注意：需要显式传入 user（跨线程场景下无法读 session）。
    """
    labels: set[str] = set()
    if not user:
        return labels
    labels.add(user['username'])
    if node_name:
        cred = _db().get_credential(user['id'], node_name)
        if cred and cred.get('username'):
            labels.add(cred['username'])
    return labels


def _my_labels(node_name: str | None) -> set[str]:
    """当前登录用户版本（仅限请求上下文内调用）。"""
    return _labels_for(get_current_user(), node_name)


def _worker_user_id(node_id: str) -> str:
    """提交任务时的 worker 侧 user_id：优先用该节点的凭据用户名。"""
    username = _current_username()
    node_name = _node_name(node_id)
    if node_name:
        user = get_current_user()
        cred = _db().get_credential(user['id'], node_name) if user else None
        if cred and cred.get('username'):
            return cred['username']
    return username


def _is_admin() -> bool:
    user = get_current_user()
    return bool(user and user.get('role') == 'admin')


def _forward(node_id: str, path: str, params: dict | None = None,
             method: str = 'GET', body: dict | None = None,
             timeout: int = 30):
    """统一转发，节点不存在/离线时抛 ValueError。"""
    pool = Nodes_Pool.get_nodes_pool()
    if method.upper() == 'GET':
        return pool.forward_get_to_node(node_id, path, params=params, timeout=timeout)
    return pool.forward_to_node(node_id, path, body or {}, method=method, timeout=timeout)


def _queue_payload(node_id: str, labels: set[str] | None = None,
                   timeout: int = 10):
    """队列快照；labels 非 None 时只保留属主在 labels 内的任务。

    worker 0.5.0 起队列混有 neu-sbox acquire 的生命周期记录
    （kind='acquire'，状态 active/released，无 task_id/command）；
    终端沙盒在左栏「活跃沙盒」展示，队列只保留命令任务。
    """
    resp = _forward(node_id, '/tasks', timeout=timeout)
    payload = resp.json()
    if isinstance(payload, dict) and 'queue' in payload:
        queue = [t for t in payload['queue'] if t.get('kind') != 'acquire']
        if labels is not None:
            queue = [t for t in queue if t.get('user_id') in labels]
        payload['queue'] = queue
    return resp, payload


def _sandbox_owners(node_id: str) -> dict[str, str]:
    """节点上所有沙盒的 name → owner 映射（删除归属校验用）。"""
    try:
        resp = _forward(node_id, '/sandbox/list', timeout=10)
        data = resp.json() if resp.ok else {}
    except ValueError:
        return {}
    return {
        s['name']: (s.get('owner') or '')
        for s in data.get('sandboxes') or [] if s.get('name')
    }


def _task_owner(node_id: str, task_id: str) -> str | None:
    """查询单个任务的归属用户名；查询失败返回 None。"""
    try:
        resp = _forward(node_id, f'/tasks/{task_id}', timeout=10)
        payload = resp.json()
        if isinstance(payload, dict):
            return payload.get('user_id')
    except ValueError:
        pass
    return None


# ═══════════════════════════════════════════════════════════════
# 提交
# ═══════════════════════════════════════════════════════════════

@tasks_bp.route('', methods=['POST'])
@login_required
def create_task():
    """提交命令到指定 Worker 的任务队列。

    user_id 由服务端注入当前登录用户名（忽略请求体中的 user_id）。
    """
    data = request.get_json(silent=True) or {}
    if not data:
        return {'error': '请求体不能为空'}, 400

    node_id = (data.get('node_id') or '').strip()
    if not node_id:
        return {'error': 'node_id 不能为空'}, 400

    command = (data.get('command') or '').strip()
    if not command:
        return {'error': '命令不能为空'}, 400

    username = _current_username()
    if not username:
        return {'error': '登录状态异常，请重新登录'}, 401

    req = {
        'command': command,
        'user_id': _worker_user_id(node_id),
        'cpu': data.get('cpu', 0),
        'memory': data.get('memory', 0),
        'mem_unit': data.get('mem_unit', 'GB'),
        'device_num': data.get('device_num', 0),
        'device_ids': data.get('device_ids'),
        'est_time': data.get('est_time', 0),
        'target': data.get('target'),
        'priority': data.get('priority', 0),
    }

    try:
        resp = _forward(node_id, '/tasks', method='POST', body=req)
        payload = resp.json()
    except ValueError as e:
        return {'error': f'{e}。请检查是否选择了正确的节点'}, 404

    if resp.ok and isinstance(payload, dict) and payload.get('task_id'):
        try:
            _db().record_submission(
                payload['task_id'], node_id, username, command=command[:2000],
            )
        except Exception as exc:  # 登记失败不影响提交本身
            logger.warning('登记任务提交失败 %s: %s', payload['task_id'], exc)

    return payload, resp.status_code


# ═══════════════════════════════════════════════════════════════
# 查询
# ═══════════════════════════════════════════════════════════════

@tasks_bp.route('', methods=['GET'])
@login_required
def list_tasks():
    """主队列：指定 Worker 的全量任务队列，全员可见。

    Query: ?node_id=xxx&mine=1   （mine=1 时仅返回当前用户的任务）
    """
    node_id = (request.args.get('node_id') or '').strip()
    if not node_id:
        return {'error': 'node_id 参数必填'}, 400

    labels = _my_labels(_node_name(node_id)) \
        if request.args.get('mine') == '1' else None
    try:
        resp, payload = _queue_payload(node_id, labels)
    except ValueError as e:
        return {'error': f'{e}。请检查是否选择了正确的节点'}, 404
    return payload, resp.status_code


@tasks_bp.route('/mine', methods=['GET'])
@login_required
def list_my_tasks():
    """我的队列：仅当前用户名下的任务。

    Query:
      ?node_id=xxx  → 单节点视图，与主队列同构: {"queue": [...]}
      （无 node_id） → 跨节点聚合:
        {"groups": [{node_id, node_name, node_status, tasks: [...]}],
         "offline_nodes": [{node_id, node_name}], "total": N}
    """
    username = _current_username()
    node_id = (request.args.get('node_id') or '').strip()

    if node_id:
        labels = _my_labels(_node_name(node_id))
        try:
            resp, payload = _queue_payload(node_id, labels)
        except ValueError as e:
            return {'error': f'{e}。请检查是否选择了正确的节点'}, 404
        return payload, resp.status_code

    # 跨节点聚合：并发拉取在线节点，单节点失败不影响其他节点
    pool = Nodes_Pool.get_nodes_pool()
    user = get_current_user()
    nodes = pool.get_all_nodes()
    online = [n for n in nodes if n.get('status') == 'online']
    offline_nodes = [
        {'node_id': n['node_id'], 'node_name': n['name']}
        for n in nodes if n.get('status') != 'online'
    ]

    def _fetch(node: dict) -> dict | None:
        try:
            labels = _labels_for(user, node['name'])
            resp, payload = _queue_payload(node['node_id'], labels)
            if not resp.ok:
                payload = {}
            queue = payload.get('queue', []) if isinstance(payload, dict) else []
            return {
                'node_id': node['node_id'],
                'node_name': node['name'],
                'node_status': 'online',
                'tasks': queue,
            }
        except ValueError:
            return None

    groups: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(online)))) as ex:
        for result in ex.map(_fetch, online):
            if result is not None:
                groups.append(result)
    groups.sort(key=lambda g: g['node_name'])
    total = sum(len(g['tasks']) for g in groups)
    return {'groups': groups, 'offline_nodes': offline_nodes,
            'total': total}, 200


@tasks_bp.route('/<task_id>', methods=['GET'])
@login_required
def get_task(task_id: str):
    """查看任务结果元数据（状态、返回码等），全员可见。

    Query: ?node_id=xxx
    """
    node_id = (request.args.get('node_id') or '').strip()
    if not node_id:
        return {'error': 'node_id 参数必填'}, 400

    try:
        resp = _forward(node_id, f'/tasks/{task_id}')
        return resp.json(), resp.status_code
    except ValueError as e:
        return {'error': f'{e}。请检查是否选择了正确的节点'}, 404


@tasks_bp.route('/<task_id>/log', methods=['GET'])
@login_required
def get_task_log(task_id: str):
    """获取任务日志（分块）。

    Query: ?node_id=xxx&tail=N 或 &offset=N&limit=M&raw=1
    仅任务本人或 admin 可看（NEU_BOX_LOGS_SHARED=1 时放开）。
    """
    node_id = (request.args.get('node_id') or '').strip()
    if not node_id:
        return {'error': 'node_id 参数必填'}, 400

    if not _LOGS_SHARED:
        owner = _task_owner(node_id, task_id)
        # owner 为 None 表示查不到归属（任务不存在/节点异常），
        # 由下方 worker 转发返回 404/错误，不在此拦截
        if owner is not None and not _is_admin() \
                and owner not in _my_labels(_node_name(node_id)):
            return {'error': '只能查看自己任务的日志'}, 403

    params = {}
    for key in ('offset', 'limit', 'tail', 'raw'):
        val = request.args.get(key)
        if val is not None:
            params[key] = val

    try:
        resp = _forward(node_id, f'/tasks/{task_id}/log', params=params or None)
        if request.args.get('raw'):
            return resp.text, resp.status_code, {
                'Content-Type': resp.headers.get('Content-Type', 'text/plain'),
            }
        content_type = resp.headers.get('Content-Type', '')
        if 'json' in content_type:
            return resp.json(), resp.status_code
        # worker 返回非 JSON（纯文本日志）时透传文本
        return resp.text, resp.status_code, {
            'Content-Type': content_type or 'text/plain',
        }
    except ValueError as e:
        return {'error': f'{e}。请检查是否选择了正确的节点'}, 404


# ═══════════════════════════════════════════════════════════════
# 删除（逐条归属校验）
# ═══════════════════════════════════════════════════════════════

@tasks_bp.route('', methods=['DELETE'])
@login_required
def delete_tasks():
    """批量删除任务/终端沙盒，转发到指定 Worker。

    Body: { "node_id": "...", "task_ids": [...] }

    task_ids 可混合命令任务 ID 与沙盒名（sbx_*.slice）：
    沙盒走 /sandbox/release 销毁（会终止其中进程）。

    非 admin 只能删除自己的任务/沙盒：逐条校验，全部被拒时
    返回 403，部分被拒时删除允许的并附带 denied 列表。
    """
    data = request.get_json(silent=True) or {}
    node_id = (data.get('node_id') or '').strip()
    if not node_id:
        return {'error': 'node_id 不能为空'}, 400

    task_ids = data.get('task_ids') or []
    if not task_ids:
        return {'error': 'task_ids 不能为空'}, 400

    # 终端沙盒名与任务 ID 分流：沙盒走 release，不是任务删除
    sandbox_ids = {
        tid for tid in task_ids
        if tid.startswith('sbx_') and tid.endswith('.slice')
    }
    task_ids_only = [tid for tid in task_ids if tid not in sandbox_ids]

    denied: list[str] = []
    if not _is_admin():
        owners = _task_owners(node_id, task_ids_only)
        labels = _my_labels(_node_name(node_id))
        sbx_owners = _sandbox_owners(node_id) if sandbox_ids else {}
        allowed = [
            tid for tid in task_ids_only if owners.get(tid) in labels
        ]
        denied += [tid for tid in task_ids_only if tid not in allowed]
        allowed += [tid for tid in sandbox_ids if sbx_owners.get(tid) in labels]
        denied += [tid for tid in sandbox_ids if sbx_owners.get(tid) not in labels]
        if not allowed:
            return {'error': '只能删除自己的任务/沙盒（被拒: %s）' % ', '.join(denied)}, 403
    else:
        allowed = list(task_ids)

    deleted = 0
    if task_ids_only:
        try:
            resp = _forward(node_id, '/tasks', method='DELETE',
                            body={'task_ids': [t for t in allowed
                                                if t not in sandbox_ids]})
            payload = resp.json() if resp.ok else {}
        except ValueError as e:
            return {'error': f'{e}。请检查是否选择了正确的节点'}, 404
        if resp.ok and isinstance(payload, dict):
            deleted += payload.get('deleted', 0)

    for name in [tid for tid in allowed if tid in sandbox_ids]:
        try:
            resp = _forward(node_id, '/sandbox/release', method='POST',
                            body={'sandbox_name': name})
        except ValueError:
            resp = None
        if resp is not None and resp.ok:
            deleted += 1
        else:
            denied.append(name)

    message = f'已删除 {deleted} 项'
    if denied:
        message += f'，{len(denied)} 项失败/被拒'
    payload = {'deleted': deleted, 'message': message}
    if denied:
        payload['denied'] = denied
    return payload, 200


def _task_owners(node_id: str, task_ids: list[str]) -> dict[str, str | None]:
    """批量查询任务归属：先拉队列快照，缺失的再逐个取元数据。"""
    owners: dict[str, str | None] = {}
    try:
        resp = _forward(node_id, '/tasks', timeout=10)
        payload = resp.json() if resp.ok else {}
        queue = payload.get('queue', []) if isinstance(payload, dict) else []
        wanted = set(task_ids)
        for t in queue:
            tid = t.get('task_id')
            if tid in wanted:
                owners[tid] = t.get('user_id')
    except ValueError:
        pass

    for tid in task_ids:
        if tid not in owners:
            owners[tid] = _task_owner(node_id, tid)
    return owners
