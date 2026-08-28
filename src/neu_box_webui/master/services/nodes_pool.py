import logging
import threading
import time
import uuid

import requests

from neu_box_webui import API_VERSION

from neu_box_webui.master.paths import nodes_config_path

logger = logging.getLogger(__name__)


class Nodes:
    """单个节点的完整信息"""

    def __init__(self, node_id: str, name: str, ip: str, port: int):
        self.node_id = node_id
        self.name = name
        self.ip = ip
        self.port = port

        # ── 动态状态（由心跳或主动查询更新） ──
        self.status = 'offline'          # online | offline
        self.status_error = ''           # 最近一次状态查询错误
        self.total_cpu = 0
        self.idle_cpu = 0
        self.total_mem = 0               # bytes
        self.idle_mem = 0                # bytes
        self.total_devices = 0
        self.idle_devices = 0
        self.dev_status = {}     # {minor: 1/0}, 1=忙碌 0=空闲
        self.active_sandboxes = 0
        self.last_heartbeat = 0.0        # timestamp
        self.worker_api_version = None     # worker /status 上报的 api_version

    def apply_status(self, data: dict):
        """用 worker /status 返回的数据更新本节点状态"""
        self.status = data.get('status', self.status)
        self.total_cpu = data.get('total_cpu', self.total_cpu)
        self.idle_cpu = data.get('idle_cpu', self.idle_cpu)
        self.total_mem = data.get('total_mem', self.total_mem)
        self.idle_mem = data.get('idle_mem', self.idle_mem)
        self.total_devices = data.get('total_devices', self.total_devices)
        self.idle_devices = data.get('idle_devices', self.idle_devices)
        self.dev_status = data.get('dev_status', self.dev_status)
        self.active_sandboxes = data.get('active_sandboxes', self.active_sandboxes)
        api_version = data.get('api_version')
        if isinstance(api_version, int):
            self.worker_api_version = api_version
            if api_version != API_VERSION:
                logger.warning(
                    '节点 %s API 版本不匹配: worker=%s webui=%s',
                    self.name, api_version, API_VERSION,
                )
        self.last_heartbeat = time.time()


class Nodes_Pool:
    """节点池（单例）—— 管理所有 worker 节点"""

    _instance = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self.nodes: dict[str, Nodes] = {}    # node_id → Nodes
        self._config_path = str(nodes_config_path())
        self._nodes_lock = threading.RLock()
        self._sync_lock = threading.Lock()
        self._http_local = threading.local()

    def _get_http_session(self) -> requests.Session:
        """每个线程复用独立 Session，节点控制面请求不继承系统代理。"""
        session = getattr(self._http_local, 'session', None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            self._http_local.session = session
        return session

    def _request(self, method: str, url: str, **kwargs) -> requests.Response:
        return self._get_http_session().request(method, url, **kwargs)

    @classmethod
    def get_nodes_pool(cls) -> 'Nodes_Pool':
        with cls._instance_lock:
            if cls._instance is None:
                obj = cls()
                obj._init_from_config()
                cls._instance = obj
        return cls._instance

    # ── 初始化 & 同步 ──────────────────────────────────────────

    def _init_from_config(self):
        """从 nodes.json 首次加载节点。"""
        self._sync_nodes_from_config()

    def _read_config_file(self) -> list[dict] | None:
        """读取 nodes.json 中 nodes_pool 数组；读取失败时保留现有节点。"""
        import json
        try:
            with open(self._config_path) as f:
                cfg = json.load(f)
            if not isinstance(cfg, dict):
                raise ValueError('节点配置根对象必须是 JSON 对象')
            nodes = cfg.get('nodes_pool', [])
            if not isinstance(nodes, list):
                raise ValueError('nodes_pool 必须是数组')
            return nodes
        except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError) as exc:
            logger.error('读取节点配置失败，保留现有节点: %s', exc)
            return None

    def sync_from_config(self):
        """公开方法：主动从 nodes.json 同步节点（供 API 调用）。"""
        self._sync_nodes_from_config()

    def _sync_nodes_from_config(self):
        """从 nodes.json 同步节点：按 name 去重，已存在的保留 node_id，不存在的删除。"""
        # 覆盖“旧配置先读、新配置先应用、旧配置后覆盖”的乱序同步。
        with self._sync_lock:
            nodes_cfg = self._read_config_file()
            if nodes_cfg is None:
                return

            with self._nodes_lock:
                # 按 name 建索引，方便查找
                old_by_name: dict[str, Nodes] = {}
                for node in self.nodes.values():
                    if node.name:
                        old_by_name[node.name] = node

                new_nodes: dict[str, Nodes] = {}
                for entry in nodes_cfg:
                    if not isinstance(entry, dict):
                        continue
                    host = str(entry.get('host') or '').strip()
                    port = entry.get('port', 5000)
                    name = str(entry.get('name') or '').strip()
                    if not host or not name:
                        continue

                    existing = old_by_name.get(name)
                    if existing:
                        existing.ip = host
                        existing.port = port
                        new_nodes[existing.node_id] = existing
                    else:
                        node_id = str(uuid.uuid4())
                        node = Nodes(node_id, name, host, port)
                        new_nodes[node_id] = node
                        logger.info(
                            '新增节点 %s (%s @ %s:%s)',
                            node_id, name, host, port,
                        )

                # 移除不再存在于 config 中的节点
                for node_id in list(self.nodes.keys()):
                    if node_id not in new_nodes:
                        logger.info('移除节点 %s', node_id)
                self.nodes = new_nodes

    # ── CRUD ───────────────────────────────────────────────────

    def add_node(self, node: Nodes):
        with self._nodes_lock:
            self.nodes[node.node_id] = node

    def remove_node(self, node_id: str):
        with self._nodes_lock:
            self.nodes.pop(node_id, None)

    def get_node_by_id(self, node_id: str) -> Nodes | None:
        with self._nodes_lock:
            return self.nodes.get(node_id)

    def get_node_by_host_port(self, host: str, port: int) -> Nodes | None:
        """按 host + 端口查找节点，用于去重。"""
        with self._nodes_lock:
            nodes = list(self.nodes.values())
        for node in nodes:
            if node.ip == host and node.port == port:
                return node
        return None

    # ── 供前端/API 调用的列表 ─────────────────────────────────

    def get_all_nodes(self) -> list[dict]:
        """返回所有节点摘要，供前端选择器使用"""
        result = []
        with self._nodes_lock:
            nodes = list(self.nodes.values())
        for node in nodes:
            result.append({
                'node_id': node.node_id,
                'name': node.name,
                'ip': node.ip,
                'port': node.port,
                'status': node.status,
                'status_error': node.status_error,
                'total_cpu': node.total_cpu,
                'idle_cpu': node.idle_cpu,
                'total_mem': node.total_mem,
                'idle_mem': node.idle_mem,
                'total_devices': node.total_devices,
                'idle_devices': node.idle_devices,
                'dev_status': node.dev_status,
                'active_sandboxes': node.active_sandboxes,
            })
        return result

    # ── 状态采集 ───────────────────────────────────────────────

    def query_node_status(self, node_id: str) -> dict:
        """主动向 worker 查询实时状态并更新本地记录"""
        node = self.get_node_by_id(node_id)
        if not node:
            raise ValueError(f'节点 {node_id} 不存在')

        return self._query_node_status(node)

    def _query_node_status(self, node: Nodes) -> dict:
        """查询稳定的节点对象快照；节点池并发同步时不会重新按 ID 查找。"""
        try:
            resp = self._request(
                'GET',
                f'http://{node.ip}:{node.port}/status',
                timeout=5,
            )
            resp.raise_for_status()
            data = resp.json()
            was_offline = node.status != 'online'
            node.apply_status(data)
            if was_offline:
                logger.info('节点恢复在线: %s (%s:%s)', node.name, node.ip, node.port)
            node.status = 'online'
            node.status_error = ''
            return data
        except requests.RequestException as e:
            node.status = 'offline'
            details = str(e)
            if node.status_error != details:
                logger.warning(
                    '节点离线: %s (%s:%s): %s',
                    node.name, node.ip, node.port, details,
                )
            node.status_error = details
            return {'error': '无法连接到节点', 'details': details}

    def query_all_nodes_status(self) -> dict[str, dict]:
        """批量查询所有节点状态"""
        results = {}
        with self._nodes_lock:
            nodes = list(self.nodes.items())
        for node_id, node in nodes:
            results[node_id] = self._query_node_status(node)
        return results

    # ── 定期轮询 ───────────────────────────────────────────────

    _polling: bool = False
    _poll_thread: threading.Thread | None = None
    _poll_interval: int = 15         # 默认每 15 秒轮询一次

    def start_polling(self, interval: int = 15):
        """启动后台轮询线程，定期查询所有 worker 的 /status。"""
        if Nodes_Pool._polling:
            return
        Nodes_Pool._poll_interval = interval
        Nodes_Pool._polling = True
        Nodes_Pool._poll_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name='node-status-poller',
        )
        Nodes_Pool._poll_thread.start()
        logger.info('后台轮询已启动，每 %ss 查询所有节点状态', interval)

    def stop_polling(self):
        """停止后台轮询。"""
        Nodes_Pool._polling = False
        if Nodes_Pool._poll_thread:
            Nodes_Pool._poll_thread.join(timeout=2)

    def _poll_loop(self):
        while Nodes_Pool._polling:
            t0 = time.monotonic()
            self._poll_all_nodes()
            # 用实际耗时修正 sleep，保证轮询间隔稳定
            elapsed = time.monotonic() - t0
            sleep_time = max(0, Nodes_Pool._poll_interval - elapsed)
            time.sleep(sleep_time)

    def _poll_all_nodes(self):
        """向所有节点请求 /status 并更新本地记录。每次先同步 nodes.json 中的节点列表。"""
        self._sync_nodes_from_config()
        with self._nodes_lock:
            nodes = list(self.nodes.values())
        for node in nodes:
            self._query_node_status(node)

    # ── 通用转发 ────────────────────────────────────────────────

    def forward_to_node(self, node_id: str, endpoint: str,
                        req: dict, timeout: int = 30,
                        method: str = 'POST') -> requests.Response:
        """向指定 worker 节点转发请求。

        Args:
            node_id:  目标节点 UUID
            endpoint: Worker 上的路径，如 '/tasks'
            req:      JSON 请求体
            timeout:  HTTP 超时秒数
            method:   HTTP 方法（POST 或 DELETE）

        Returns:
            requests.Response 对象
        """
        node = self.get_node_by_id(node_id)
        if not node:
            raise ValueError(f'节点 {node_id} 不存在')
        try:
            resp = self._request(
                method,
                f'http://{node.ip}:{node.port}{endpoint}',
                json=req,
                timeout=timeout,
            )
            logger.debug(
                '%s → %s %s 状态 %s',
                method, node_id, endpoint, resp.status_code,
            )
            if not resp.ok and resp.text and resp.text.strip().startswith('<!'):
                raise ValueError(f'Worker 返回错误: {resp.text[:200]}')
            return resp
        except requests.RequestException as e:
            raise ValueError(f'无法连接节点 {node_id}: {e}')

    def forward_get_to_node(self, node_id: str, endpoint: str,
                            params: dict = None, timeout: int = 30) -> requests.Response:
        """向指定 worker 节点转发 GET 请求。

        Args:
            node_id:  目标节点 UUID
            endpoint: Worker 上的路径，如 '/tasks'
            params:   URL 查询参数
            timeout:  HTTP 超时秒数

        Returns:
            requests.Response 对象
        """
        node = self.get_node_by_id(node_id)
        if not node:
            raise ValueError(f'节点 {node_id} 不存在')
        try:
            resp = self._request(
                'GET',
                f'http://{node.ip}:{node.port}{endpoint}',
                params=params,
                timeout=timeout,
            )
            logger.debug('GET → %s %s 状态 %s', node_id, endpoint, resp.status_code)
            if not resp.ok and resp.text and resp.text.strip().startswith('<!'):
                raise ValueError(f'Worker 返回错误: {resp.text[:200]}')
            return resp
        except requests.RequestException as e:
            raise ValueError(f'无法连接节点 {node_id}: {e}')
