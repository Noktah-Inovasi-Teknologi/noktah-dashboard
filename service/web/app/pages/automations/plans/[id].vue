<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { PlanDetail, PlanRow } from '~/types/hub'

/**
 * One Content Plan (US1, US2): rows per Bentuk against the Registry quota, what blocks a
 * Greenlight and what only warns (FR-011), the rows and their issues, change flags with
 * old → new (FR-020), and the Greenlight itself (FR-012). A Greenlight carries the plan's
 * fingerprint as the manager saw it; a plan changed since is refused (409) and reloaded.
 */
const route = useRoute()
const toast = useToast()
const id = computed(() => String(route.params.id))
const { data: plan, error, refresh } = await useFetch<PlanDetail>(() => `/api/hub/v1/content-plans/${id.value}`)
useSeoMeta({ title: () => (plan.value ? `Content Plan ${plan.value.client.name} · Noktah Hub` : 'Content Plan · Noktah Hub') })

const quota = computed(() => Object.entries(plan.value?.quota ?? {}).map(([bentuk, q]) => {
  const diff = q.quota === null || q.quota === undefined ? null : q.planned - q.quota
  return { bentuk, ...q, diff }
}))

const { page, pageRows, pageSize, total } = usePaged(computed(() => plan.value?.rows ?? []))
const rowsLine = computed(() => {
  const p = plan.value
  if (!p) return ''
  return p.rows_without_issue === undefined
    ? `${p.rows.length} baris konten.`
    : `${p.rows.length} baris konten, ${p.rows_without_issue} belum punya issue.`
})
const columns: TableColumn<PlanRow>[] = [
  { accessorKey: 'row_number', header: 'Baris', meta: { class: { th: 'w-px', td: 'w-px tabular-nums text-muted' } } },
  { accessorKey: 'tanggal', header: 'Tanggal', meta: { class: { th: 'hidden sm:table-cell', td: 'hidden sm:table-cell whitespace-nowrap' } } },
  { accessorKey: 'bentuk', header: 'Bentuk', meta: { class: { th: 'hidden md:table-cell', td: 'hidden md:table-cell whitespace-nowrap' } } },
  { accessorKey: 'topik', header: 'Topik', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'key', header: 'Key', meta: { class: { th: 'w-px', td: 'w-px whitespace-nowrap' } } }
]

// ── actions ─────────────────────────────────────────────────────────────────────────
const busy = ref<'greenlight' | 'rescan' | string | null>(null)
async function greenlight() {
  if (!plan.value) return
  busy.value = 'greenlight'
  try {
    plan.value = await $fetch<PlanDetail>(`/api/hub/v1/content-plans/${id.value}/greenlight`, {
      method: 'POST', body: { fingerprint: plan.value.fingerprint }
    })
    toast.add({ color: 'success', title: 'Greenlight diberikan', description: 'Issue Content Plan ini sekarang bisa dibuat dari halaman Otomasi.' })
  } catch (err) {
    const e = hubError(err)
    if (e.status === 409) {
      toast.add({ color: 'warning', title: 'Content Plan berubah', description: e.message })
      await refresh()
    } else {
      toast.add({ color: 'error', title: 'Belum bisa di-Greenlight', description: e.message })
    }
  } finally {
    busy.value = null
  }
}
async function rescan() {
  busy.value = 'rescan'
  try {
    await $fetch(`/api/hub/v1/content-plans/${id.value}/rescan`, { method: 'POST' })
    toast.add({ color: 'success', title: 'Pemeriksaan dimulai', description: 'Muat ulang halaman ini satu atau dua menit lagi.' })
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    busy.value = null
  }
}
async function resolve(flagId: string) {
  busy.value = flagId
  try {
    plan.value = await $fetch<PlanDetail>(`/api/hub/v1/content-plans/flags/${flagId}/resolve`, { method: 'POST' })
    toast.add({ color: 'success', title: 'Ditandai selesai' })
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    busy.value = null
  }
}
const back = computed(() => `/automations?tab=jira${plan.value ? `&month=${plan.value.month}` : ''}`)
</script>

<template>
  <UDashboardPanel id="plan">
    <template #header>
      <UDashboardNavbar :title="plan ? `Content Plan ${plan.client.name}` : 'Content Plan'">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            :to="back"
            color="neutral"
            variant="ghost"
            icon="i-lucide-arrow-left"
            label="Otomasi"
          />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UAlert
        v-if="error"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(error).message"
      />
      <div
        v-else-if="plan"
        class="space-y-4"
      >
        <div class="flex flex-col gap-3 lg:flex-row lg:items-start">
          <div class="min-w-0 flex-1 space-y-1">
            <div class="flex flex-wrap items-center gap-2">
              <h2 class="text-lg font-semibold text-highlighted">
                {{ plan.client.name }} · {{ monthLabel(plan.month) }}
              </h2>
              <UBadge
                :color="planStatus(plan.status).color"
                variant="subtle"
                :label="planStatus(plan.status).label"
              />
            </div>
            <p class="text-sm text-muted wrap-break-word">
              <ULink
                v-if="plan.file_url"
                :to="plan.file_url"
                target="_blank"
                class="text-primary hover:underline"
              >
                {{ plan.file_name ?? 'Buka di Google Sheets' }}
              </ULink>
              <template v-else>
                {{ plan.file_name ?? 'Belum ada file' }}
              </template>
              · diperiksa {{ formatDateTime(plan.scanned_at) }}
            </p>
            <p
              v-if="plan.team"
              class="text-sm text-muted"
            >
              Field Associate: {{ plan.team.field_associate ?? '—' }} · Content Editor: {{ plan.team.content_editor ?? '—' }}
            </p>
          </div>
          <div class="flex flex-wrap gap-2">
            <UButton
              color="neutral"
              variant="outline"
              icon="i-lucide-refresh-cw"
              label="Periksa ulang"
              :loading="busy === 'rescan'"
              @click="rescan"
            />
            <UButton
              icon="i-lucide-check-check"
              label="Greenlight"
              :disabled="!plan.can_greenlight"
              :loading="busy === 'greenlight'"
              @click="greenlight"
            />
          </div>
        </div>
        <p class="text-sm text-muted">
          Greenlight berarti issue boleh dibuat dari Content Plan ini. Bila Content Plan berubah sebelum issue-nya dibuat, perlu Greenlight lagi.
        </p>

        <UAlert
          v-if="plan.state !== 'found' && plan.problem"
          color="error"
          variant="subtle"
          icon="i-lucide-file-x"
          :title="planStatus(plan.status).label"
          :description="plan.problem"
        />

        <div
          v-if="quota.length"
          class="grid gap-4 sm:grid-cols-3"
        >
          <UCard
            v-for="q in quota"
            :key="q.bentuk"
          >
            <p class="text-sm text-muted">
              {{ q.bentuk }}
            </p>
            <p class="mt-1 text-2xl font-semibold text-highlighted tabular-nums">
              {{ q.planned }}<span class="text-base font-normal text-muted">/{{ q.quota ?? '—' }}</span>
            </p>
            <UBadge
              v-if="q.diff !== null && q.diff !== 0"
              color="warning"
              variant="subtle"
              size="sm"
              class="mt-2"
              :label="q.diff < 0 ? `Kurang ${-q.diff}` : `Lebih ${q.diff}`"
            />
            <UBadge
              v-else-if="q.diff === 0"
              color="success"
              variant="subtle"
              size="sm"
              class="mt-2"
              label="Sesuai kuota"
            />
            <p
              v-else
              class="mt-2 text-xs text-muted"
            >
              Kuota belum diisi di Registry
            </p>
          </UCard>
        </div>

        <UAlert
          v-if="plan.blocking.length"
          color="error"
          variant="subtle"
          icon="i-lucide-octagon-x"
          title="Belum bisa di-Greenlight"
        >
          <template #description>
            <ul class="mt-1 list-disc space-y-1 ps-4">
              <li
                v-for="(b, i) in plan.blocking"
                :key="`${b.code}-${i}`"
              >
                {{ b.message }}
              </li>
            </ul>
          </template>
        </UAlert>
        <UAlert
          v-if="plan.warnings.length"
          color="warning"
          variant="subtle"
          icon="i-lucide-triangle-alert"
          title="Perlu dicek, tetapi tidak menghalangi Greenlight"
        >
          <template #description>
            <ul class="mt-1 list-disc space-y-1 ps-4">
              <li
                v-for="(w, i) in plan.warnings"
                :key="`${w.code}-${i}`"
              >
                {{ w.message }}
              </li>
            </ul>
          </template>
        </UAlert>

        <UCard
          v-if="plan.flags.length"
          :ui="{ body: 'space-y-4' }"
        >
          <template #header>
            <h3 class="font-medium text-highlighted">
              Perubahan yang perlu dicek
            </h3>
            <p class="mt-1 text-sm text-muted">
              Isi issue di Jira tidak diubah Hub. Perubahan dikirim sebagai komentar di issue-nya; perbarui issue di Jira bila perlu, lalu tandai selesai.
            </p>
          </template>
          <div
            v-for="f in plan.flags"
            :key="f.id"
            class="space-y-2 border-b border-default pb-4 last:border-b-0 last:pb-0"
          >
            <div class="flex flex-wrap items-center gap-2">
              <UBadge
                :color="flagKind(f.kind).color"
                variant="subtle"
                :label="flagKind(f.kind).label"
              />
              <ULink
                v-if="f.issue_key"
                :to="`${JIRA_BROWSE}${f.issue_key}`"
                target="_blank"
                class="text-sm font-medium text-primary hover:underline"
              >
                {{ f.issue_key }}
              </ULink>
              <span
                v-if="f.row_number"
                class="text-sm text-muted"
              >baris {{ f.row_number }}</span>
              <UBadge
                v-if="COMMENT_STATE[f.comment_state]"
                :color="COMMENT_STATE[f.comment_state]!.color"
                variant="outline"
                size="sm"
                :label="COMMENT_STATE[f.comment_state]!.label"
              />
              <span class="text-xs text-muted">{{ formatDateTime(f.detected_at) }}</span>
              <UButton
                color="neutral"
                variant="outline"
                size="sm"
                icon="i-lucide-check"
                label="Tandai selesai"
                class="sm:ms-auto"
                :loading="busy === f.id"
                @click="resolve(f.id)"
              />
            </div>
            <ul
              v-if="f.changes.length"
              class="space-y-1"
            >
              <li
                v-for="c in f.changes"
                :key="c.column"
                class="text-sm wrap-break-word"
              >
                <span class="font-medium text-highlighted">{{ `${c.column}: ` }}</span>
                <span class="text-muted line-through">{{ c.old || '(kosong)' }}</span>
                →
                <span>{{ c.new || '(kosong)' }}</span>
              </li>
            </ul>
          </div>
        </UCard>

        <UCard :ui="{ body: 'p-0 sm:p-0' }">
          <template #header>
            <h3 class="font-medium text-highlighted">
              Baris
            </h3>
            <p class="mt-1 text-sm text-muted">
              {{ rowsLine }}
            </p>
          </template>
          <UTable
            :data="pageRows"
            :columns="columns"
            empty="Belum ada baris konten di Content Plan ini."
          >
            <template #topik-cell="{ row }">
              <span class="block min-w-36">{{ row.original.topik ?? '—' }}</span>
              <span class="mt-0.5 block text-xs text-muted md:hidden">
                <span class="sm:hidden">{{ row.original.tanggal ?? '—' }} · </span>{{ row.original.bentuk ?? '—' }}
              </span>
              <div
                v-if="row.original.flags.length"
                class="mt-1 flex flex-wrap gap-1"
              >
                <UBadge
                  v-for="k in row.original.flags"
                  :key="k"
                  :color="flagKind(k).color"
                  variant="subtle"
                  size="sm"
                  :label="flagKind(k).label"
                />
              </div>
            </template>
            <template #key-cell="{ row }">
              <ULink
                v-if="row.original.issue_key"
                :to="`${JIRA_BROWSE}${row.original.issue_key}`"
                target="_blank"
                class="text-sm font-medium text-primary hover:underline"
              >
                {{ row.original.issue_key }}
              </ULink>
              <span
                v-else
                class="text-sm text-muted"
              >Belum ada</span>
            </template>
          </UTable>
          <div class="px-4 pb-3 sm:px-6">
            <ListPager
              v-model:page="page"
              :total="total"
              :page-size="pageSize"
            />
          </div>
        </UCard>

        <UCard>
          <template #header>
            <h3 class="font-medium text-highlighted">
              Riwayat Greenlight
            </h3>
          </template>
          <ol
            v-if="plan.greenlights.length"
            class="space-y-1"
          >
            <li
              v-for="(g, i) in plan.greenlights"
              :key="i"
              class="text-sm"
            >
              <span class="text-highlighted">{{ g.by }}</span>
              <span class="text-muted"> · {{ formatDateTime(g.at) }}</span>
            </li>
          </ol>
          <p
            v-else
            class="text-sm text-muted"
          >
            Belum pernah di-Greenlight.
          </p>
        </UCard>
      </div>
    </template>
  </UDashboardPanel>
</template>
