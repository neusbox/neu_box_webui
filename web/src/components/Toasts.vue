<script setup>
import { toasts, dismissToast } from '../store'
import Icon from './Icon.vue'

const icons = { success: 'check', error: 'alert', info: 'info' }
</script>

<template>
  <div class="toast-stack">
    <div
      v-for="t in toasts"
      :key="t.id"
      class="toast"
      :class="t.type"
      @click="dismissToast(t.id)"
    >
      <Icon :name="icons[t.type] || 'info'" :size="14" />
      <span>{{ t.message }}</span>
    </div>
  </div>
</template>
