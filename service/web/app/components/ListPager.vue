<script setup lang="ts">
/** "1–20 dari 23" and the page buttons under a table or list; hidden when one page holds everything. */
const props = defineProps<{ total: number, pageSize: number }>()
const page = defineModel<number>('page', { required: true })
const from = computed(() => (props.total ? (page.value - 1) * props.pageSize + 1 : 0))
const to = computed(() => Math.min(page.value * props.pageSize, props.total))
</script>

<template>
  <div
    v-if="total > pageSize"
    class="mt-3 flex flex-wrap items-center justify-between gap-2"
  >
    <p class="text-sm text-muted tabular-nums">
      {{ from }}–{{ to }} dari {{ total }}
    </p>
    <UPagination
      v-model:page="page"
      :total="total"
      :items-per-page="pageSize"
      :sibling-count="1"
      size="sm"
    />
  </div>
</template>
