<script setup>
/**
 * 通用弹窗。v-model 控制显隐。
 */
const props = defineProps({
  modelValue: Boolean,
  title: { type: String, default: '' },
  wide: Boolean,
  closable: { type: Boolean, default: true },
})
const emit = defineEmits(['update:modelValue', 'close'])

function close() {
  if (!props.closable) return
  emit('update:modelValue', false)
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div v-if="modelValue" class="modal-overlay" @click.self="close">
      <div class="modal" :class="{ wide }">
        <div class="modal-head">
          <slot name="title">{{ title }}</slot>
          <button v-if="closable" class="close" @click="close" aria-label="关闭">×</button>
        </div>
        <div class="modal-body"><slot /></div>
        <div v-if="$slots.footer" class="modal-foot"><slot name="footer" /></div>
      </div>
    </div>
  </Teleport>
</template>
