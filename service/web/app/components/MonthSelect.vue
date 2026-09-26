<script setup lang="ts">
/** A month picker for Otomasi and Laporan: `YYYY-MM` in, Indonesian month names shown, newest first. */
const props = withDefaults(defineProps<{ from?: string, to: string, label?: string }>(), {
  from: '2026-01',
  label: 'Bulan'
})
const month = defineModel<string>({ required: true })
const items = computed(() => {
  const list = monthItems(props.from, props.to)
  // A month outside the range (from ?month=) still shows as picked.
  if (month.value && !list.some(i => i.value === month.value)) list.unshift({ label: monthLabel(month.value), value: month.value })
  return list
})
</script>

<template>
  <USelect
    v-model="month"
    :items="items"
    icon="i-lucide-calendar"
    class="w-full sm:w-48"
    :aria-label="label"
  />
</template>
