<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { Label } from '~/composables/useAutomationLabels'
import type { JiraBatch, PlanList, PlanSummary } from '~/types/hub'

/**
 * Otomasi → Jira (US1, FR-010…FR-019): a month's Content Plans, which are greenlit, and
 * "Buat issue Jira" for several greenlit plans at once. Creation runs in the background
 * (hub-jira-create); this polls the batch every 3 s and lists the rows Jira rejected.
 */
const month = defineModel<string>('month', { required: true })
const toast = useToast()

const { data, error, status, refresh } = await useFetch<PlanList>('/api/hub/v1/content-plans', {
  query: { month },
  default: () => ({ month: month.value, scanned_at: null, plans: [] })
})
const { data: recent, refresh: refreshRecent } = await useFetch<{ batches: JiraBatch[] }>('/api/hub/v1/jira-batches', {
  query: { limit: 5 },
  default: () => ({ batches: [] })
})

const statusOf = planStatus

// ── selection: only plans whose issues may be created now (greenlit, unchanged) ─────────
const selectable = (p: PlanSummary) => !!p.id && (p.status === 'greenlit' || p.status === 'partly_created')
const ready = computed(() => data.value.plans.filter(selectable))
const selected = ref<string[]>([])
watch(month, () => (selected.value = []))
watch(ready, list => (selected.value = selected.value.filter(id => list.some(p => p.id === id))))
const allSelected = computed(() => ready.value.length > 0 && ready.value.every(p => selected.value.includes(p.id!)))
function toggleAll(on: boolean | 'indeterminate') {
  selected.value = on === true ? ready.value.map(p => p.id!) : []
}
function toggle(id: string, on: boolean | 'indeterminate') {
  selected.value = on === true ? [...new Set([...selected.value, id])] : selected.value.filter(x => x !== id)
}

const columns: TableColumn<PlanSummary>[] = [
  { id: 'select', meta: { class: { th: 'w-px', td: 'w-px' } } },
  { id: 'client', header: 'Klien', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'status', header: 'Status', meta: { class: { th: 'hidden sm:table-cell w-px', td: 'hidden sm:table-cell w-px' } } },
  { id: 'rows', header: 'Baris', meta: { class: { th: 'hidden sm:table-cell text-right', td: 'hidden sm:table-cell text-right tabular-nums' } } },
  { id: 'issues', header: 'Issue', meta: { class: { th: 'hidden sm:table-cell text-right', td: 'hidden sm:table-cell text-right tabular-nums' } } },
  { id: 'flags', header: 'Perubahan', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell' } } },
  { id: 'greenlit', header: 'Greenlight', meta: { class: { th: 'hidden xl:table-cell', td: 'hidden xl:table-cell whitespace-normal' } } },
  { id: 'open', meta: { class: { th: 'w-px', td: 'w-px' } } }
]

// ── "Periksa sekarang" ──────────────────────────────────────────────────────────────
const scanning = ref(false)
async function scanNow() {
  scanning.value = true
  try {
    await $fetch('/api/hub/v1/content-plans/scan-month', { method: 'POST', query: { month: month.value } })
    toast.add({ color: 'success', title: 'Pemeriksaan dimulai', description: 'Hasilnya muncul di sini dalam beberapa menit.' })
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    scanning.value = false
  }
}

// ── "Buat issue Jira" and the batch's progress ──────────────────────────────────────
const refused = ref<{ client: string, message: string }[]>([])
const creating = ref(false)
const batch = ref<JiraBatch | null>(null)
let timer: ReturnType<typeof setTimeout> | undefined
const running = (b: JiraBatch | null) => !!b && (b.status === 'queued' || b.status === 'running')

async function poll(id: string) {
  clearTimeout(timer)
  try {
    batch.value = await $fetch<JiraBatch>(`/api/hub/v1/jira-batches/${id}`)
  } catch (err) {
    toast.add({ color: 'error', title: 'Progres batch tidak bisa dimuat', description: hubError(err).message })
    return
  }
  if (running(batch.value)) {
    timer = setTimeout(() => poll(id), 3000)
  } else {
    await Promise.all([refresh(), refreshRecent()])
  }
}
onBeforeUnmount(() => clearTimeout(timer))

async function createIssues() {
  creating.value = true
  refused.value = []
  try {
    const res = await $fetch<{ batch: JiraBatch }>('/api/hub/v1/jira-batches', { method: 'POST', body: { plan_ids: selected.value } })
    selected.value = []
    toast.add({ color: 'success', title: 'Pembuatan issue dimulai', description: 'Progresnya tampil di bawah.' })
    batch.value = res.batch
    await refreshRecent()
    await poll(res.batch.id)
  } catch (err) {
    const e = hubError(err)
    const plans = hubErrorBody(err).plans
    if (e.status === 422 && Array.isArray(plans)) refused.value = plans as { client: string, message: string }[]
    else toast.add({ color: 'error', title: 'Gagal', description: e.message })
  } finally {
    creating.value = false
  }
}

const BATCH_STATUS: Record<string, Label> = {
  queued: { label: 'Antre', color: 'neutral' },
  running: { label: 'Berjalan', color: 'info' },
  done: { label: 'Selesai', color: 'success' },
  failed: { label: 'Gagal', color: 'error' }
}
const batchStatus = (s: string): Label => BATCH_STATUS[s] ?? { label: s, color: 'neutral' }
const progress = computed(() => {
  const b = batch.value
  return b && b.total ? Math.round(((b.created + b.failed) / b.total) * 100) : 0
})
const problems = computed(() => (batch.value?.rows ?? []).filter(r => r.outcome !== 'created'))
</script>

<template>
  <div class="space-y-4">
    <div class="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
      <MonthSelect
        v-model="month"
        :to="monthKey(2)"
        label="Bulan Content Plan"
      />
      <UButton
        color="neutral"
        variant="outline"
        icon="i-lucide-refresh-cw"
        label="Periksa sekarang"
        :loading="scanning"
        class="justify-center"
        @click="scanNow"
      />
      <p class="text-sm text-muted sm:me-auto">
        {{ data.scanned_at ? `Diperiksa ${formatDateTime(data.scanned_at)}` : 'Belum diperiksa bulan ini' }}
      </p>
      <UButton
        icon="i-lucide-send"
        :label="selected.length ? `Buat issue Jira (${selected.length} Content Plan)` : 'Buat issue Jira'"
        :disabled="!selected.length || running(batch)"
        :loading="creating"
        class="justify-center"
        @click="createIssues"
      />
    </div>
    <p class="text-sm text-muted">
      Pilih Content Plan yang sudah di-Greenlight, lalu tekan Buat issue Jira. Hanya baris yang belum punya issue yang dibuat, jadi tombolnya aman ditekan lagi.
    </p>

    <UAlert
      v-if="refused.length"
      color="error"
      variant="subtle"
      icon="i-lucide-octagon-x"
      title="Belum ada issue yang dibuat"
      close
      @update:open="refused = []"
    >
      <template #description>
        <ul class="mt-1 space-y-1">
          <li
            v-for="p in refused"
            :key="p.client"
          >
            <span class="font-medium">{{ p.client }}</span>: {{ p.message }}
          </li>
        </ul>
      </template>
    </UAlert>

    <UCard
      v-if="batch"
      :ui="{ body: 'space-y-3' }"
    >
      <template #header>
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="font-medium text-highlighted">
            Batch {{ formatDateTime(batch.requested_at) }}
          </h3>
          <UBadge
            :color="batchStatus(batch.status).color"
            variant="subtle"
            :label="batchStatus(batch.status).label"
          />
          <UButton
            v-if="!running(batch)"
            color="neutral"
            variant="ghost"
            icon="i-lucide-x"
            aria-label="Tutup"
            class="ms-auto"
            @click="batch = null"
          />
        </div>
        <p class="mt-1 text-sm text-muted">
          Diminta {{ batch.requested_by }}
        </p>
      </template>
      <UProgress
        :model-value="progress"
        :color="batch.failed ? 'warning' : 'primary'"
      />
      <p class="text-sm tabular-nums">
        {{ batch.created }} dibuat, {{ batch.failed }} gagal, dari {{ batch.total }} baris
      </p>
      <UAlert
        v-if="batch.error"
        color="error"
        variant="subtle"
        icon="i-lucide-octagon-x"
        :title="batch.error"
      />
      <div v-if="problems.length && !running(batch)">
        <p class="mb-1 text-sm font-medium text-highlighted">
          Baris yang tidak dibuat
        </p>
        <ul class="space-y-2">
          <li
            v-for="r in problems"
            :key="`${r.plan_id ?? r.client}-${r.row_number}`"
            class="text-sm"
          >
            <span class="font-medium">{{ r.client }}</span>
            <span class="text-muted"> · baris {{ r.row_number }}</span>
            <p class="wrap-break-word text-muted">
              {{ r.reason ?? 'Jira tidak menyebutkan alasannya.' }}
            </p>
          </li>
        </ul>
        <p class="mt-2 text-sm text-muted">
          Perbaiki barisnya di Content Plan, lalu tekan Buat issue Jira lagi; hanya baris ini yang akan dibuat.
        </p>
      </div>
    </UCard>

    <UAlert
      v-if="error"
      color="error"
      icon="i-lucide-server-off"
      :title="hubError(error).message"
    />
    <UTable
      v-else
      :data="data.plans"
      :columns="columns"
      :loading="status === 'pending'"
      :empty="`Belum ada klien Eskala aktif untuk ${monthLabel(month)}.`"
    >
      <template #select-header>
        <UCheckbox
          :model-value="allSelected"
          :disabled="!ready.length"
          aria-label="Pilih semua Content Plan yang siap"
          @update:model-value="toggleAll"
        />
      </template>
      <template #select-cell="{ row }">
        <UCheckbox
          v-if="selectable(row.original)"
          :model-value="selected.includes(row.original.id!)"
          :aria-label="`Pilih ${row.original.client.name}`"
          @update:model-value="v => toggle(row.original.id!, v)"
        />
      </template>
      <template #client-cell="{ row }">
        <NuxtLink
          v-if="row.original.id"
          :to="`/automations/plans/${row.original.id}`"
          class="font-medium text-highlighted hover:underline"
        >
          {{ row.original.client.name }}
        </NuxtLink>
        <span
          v-else
          class="font-medium text-highlighted"
        >{{ row.original.client.name }}</span>
        <p
          v-if="row.original.file_name"
          class="mt-0.5 text-xs text-muted wrap-break-word"
        >
          {{ row.original.file_name }}
        </p>
        <p
          v-else-if="row.original.problem"
          class="mt-0.5 text-xs text-muted"
        >
          {{ row.original.problem }}
        </p>
        <div class="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 sm:hidden">
          <UBadge
            :color="statusOf(row.original.status).color"
            variant="subtle"
            size="sm"
            :label="statusOf(row.original.status).label"
          />
          <span class="text-xs text-muted tabular-nums">{{ row.original.rows }} baris · {{ row.original.issues }} issue</span>
        </div>
        <UBadge
          v-if="row.original.open_flags"
          color="warning"
          variant="subtle"
          size="sm"
          :label="`${row.original.open_flags} perubahan terbuka`"
          class="mt-1 md:hidden"
        />
      </template>
      <template #status-cell="{ row }">
        <UBadge
          :color="statusOf(row.original.status).color"
          variant="subtle"
          :label="statusOf(row.original.status).label"
        />
      </template>
      <template #rows-cell="{ row }">
        {{ row.original.rows }}
      </template>
      <template #issues-cell="{ row }">
        {{ row.original.issues }}
      </template>
      <template #flags-cell="{ row }">
        <UBadge
          v-if="row.original.open_flags"
          color="warning"
          variant="subtle"
          :label="`${row.original.open_flags} terbuka`"
        />
        <span
          v-else
          class="text-muted"
        >—</span>
      </template>
      <template #greenlit-cell="{ row }">
        <template v-if="row.original.greenlit">
          <span class="block text-sm">{{ row.original.greenlit.by }}</span>
          <span class="block text-xs text-muted">{{ formatDateTime(row.original.greenlit.at) }}</span>
        </template>
        <span
          v-else
          class="text-muted"
        >—</span>
      </template>
      <template #open-cell="{ row }">
        <UButton
          v-if="row.original.id"
          :to="`/automations/plans/${row.original.id}`"
          color="neutral"
          variant="ghost"
          icon="i-lucide-chevron-right"
          :aria-label="`Buka Content Plan ${row.original.client.name}`"
        />
      </template>
    </UTable>

    <UCard v-if="recent.batches.length">
      <template #header>
        <h3 class="font-medium text-highlighted">
          Batch terakhir
        </h3>
      </template>
      <ul class="divide-y divide-default">
        <li
          v-for="b in recent.batches"
          :key="b.id"
          class="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 first:pt-0 last:pb-0"
        >
          <div class="min-w-0 flex-1">
            <p class="text-sm text-highlighted">
              {{ formatDateTime(b.requested_at) }} · {{ b.requested_by }}
            </p>
            <p class="text-xs text-muted tabular-nums">
              {{ b.created }} dibuat, {{ b.failed }} gagal, dari {{ b.total }} baris
            </p>
          </div>
          <UBadge
            :color="batchStatus(b.status).color"
            variant="subtle"
            :label="batchStatus(b.status).label"
          />
          <UButton
            color="neutral"
            variant="ghost"
            size="sm"
            label="Lihat"
            @click="poll(b.id)"
          />
        </li>
      </ul>
    </UCard>
  </div>
</template>
