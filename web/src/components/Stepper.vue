<script setup>
/** 数字步进器（资源限制：0 表示“不限制”） */
import { ref, computed, watch } from 'vue'

const props = defineProps({
  modelValue: { type: Number, default: 0 },
  min: { type: Number, default: 0 },
  max: { type: Number, default: Infinity },
  noLimitText: { type: String, default: '不限制' },
})
const emit = defineEmits(['update:modelValue'])

const text = ref(String(props.modelValue || props.noLimitText))
watch(() => props.modelValue, (v) => {
  if (parse() !== v) text.value = v === 0 ? props.noLimitText : String(v)
})

function parse() {
  const raw = String(text.value).trim()
  if (raw === '' || raw === props.noLimitText) return 0
  const n = parseInt(raw, 10)
  return isNaN(n) || n < 0 ? props.modelValue : n
}

function commit() {
  let v = parse()
  if (v < props.min) v = props.min
  if (v > props.max) v = props.max
  text.value = v === 0 ? props.noLimitText : String(v)
  emit('update:modelValue', v)
}

function step(dir) {
  const next = props.modelValue + dir
  if (next < props.min || next > props.max) return
  text.value = next === 0 ? props.noLimitText : String(next)
  emit('update:modelValue', next)
}

const isNoLimit = computed(() => props.modelValue === 0)
</script>

<template>
  <div class="stepper">
    <button type="button" :disabled="modelValue <= min" @click="step(-1)" aria-label="减少">−</button>
    <span class="value" :class="{ nolimit: isNoLimit }">
      <input
        :value="text"
        inputmode="numeric"
        @blur="commit"
        @keydown.enter="$event.target.blur()"
      >
    </span>
    <button type="button" :disabled="modelValue >= max" @click="step(1)" aria-label="增加">+</button>
  </div>
</template>
