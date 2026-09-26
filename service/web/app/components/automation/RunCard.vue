<script setup lang="ts">
import type { Automation, AutomationRun } from '~/types/hub'

/** One automation's card on Otomasi (FR-040): last run, next run, failures, 90 days of runs. */
const props = defineProps<{ automation: Automation }>()

type Tone = 'success' | 'error' | 'warning' | 'info' | 'neutral'
const STATE: Record<string, { label: string, color: Tone, bar: string }> = {
  COMPLETED: { label: 'Berhasil', color: 'success', bar: 'bg-success' },
  FAILED: { label: 'Gagal', color: 'error', bar: 'bg-error' },
  CRASHED: { label: 'Gagal', color: 'error', bar: 'bg-error' },
  CANCELLED: { label: 'Dibatalkan', color: 'neutral', bar: 'bg-accented' },
  CANCELLING: { label: 'Dibatalkan', color: 'neutral', bar: 'bg-accented' },
  RUNNING: { label: 'Berjalan', color: 'info', bar: 'bg-info' },
  PENDING: { label: 'Menunggu', color: 'neutral', bar: 'bg-accented' },
  PAUSED: { label: 'Dijeda', color: 'warning', bar: 'bg-warning' }
}
const stateOf = (r: AutomationRun | null | undefined) => STATE[r?.state ?? ''] ?? { label: r?.state_name ?? r?.state ?? '—', color: 'neutral' as Tone, bar: 'bg-accented' }

const failures = computed(() => props.automation.failures
  ?? props.automation.last_run?.failures
  ?? props.automation.history.filter(r => r.state === 'FAILED' || r.state === 'CRASHED').length)
// The strip shows the latest 40 runs, oldest on the left.
const strip = computed(() => [...props.automation.history.slice(0, 40)].reverse())
const DESCRIPTION: Record<string, string> = {
  jira: 'Memeriksa Content Plan tiap 15 menit, membuat issue dari Content Plan yang sudah di-Greenlight, dan menyalin data Jira untuk Laporan.',
  harvest: 'Sebulan sekali mengumpulkan post terbaru setiap akun sosial klien dan pesaingnya di Registry, satu akun tiap 30 menit.'
}
</script>

<template>
  <UCard :ui="{ body: 'space-y-3' }">
    <template #header>
      <div class="flex flex-wrap items-center gap-2">
        <h3 class="font-medium text-highlighted">
          {{ automation.label }}
        </h3>
        <UBadge
          v-if="automation.last_run"
          :color="stateOf(automation.last_run).color"
          variant="subtle"
          :label="stateOf(automation.last_run).label"
        />
      </div>
      <p
        v-if="DESCRIPTION[automation.key]"
        class="mt-1 text-sm text-muted"
      >
        {{ DESCRIPTION[automation.key] }}
      </p>
    </template>

    <UAlert
      v-if="automation.unavailable"
      color="warning"
      variant="subtle"
      icon="i-lucide-plug-zap"
      title="Penjadwal otomasi tidak bisa dihubungi"
      description="Riwayat dan jadwal jalan tidak bisa ditampilkan sekarang. Bila penjadwal otomasi di PC kantor mati, otomasi juga berhenti; muat ulang halaman ini beberapa menit lagi."
    />
    <template v-else>
      <dl class="grid grid-cols-1 gap-x-4 gap-y-2 text-sm sm:grid-cols-3">
        <div class="min-w-0">
          <dt class="text-muted">
            Terakhir jalan
          </dt>
          <dd class="text-highlighted">
            {{ automation.last_run ? formatDateTime(automation.last_run.started_at) : 'Belum pernah' }}
          </dd>
        </div>
        <div class="min-w-0">
          <dt class="text-muted">
            Berikutnya
          </dt>
          <dd class="text-highlighted">
            {{ automation.next_run_at ? formatDateTime(automation.next_run_at) : 'Tidak terjadwal' }}
          </dd>
        </div>
        <div class="min-w-0">
          <dt class="text-muted">
            Gagal (90 hari)
          </dt>
          <dd :class="failures ? 'text-error font-medium' : 'text-highlighted'">
            {{ failures }}
          </dd>
        </div>
      </dl>
      <div v-if="strip.length">
        <div
          class="flex h-6 items-end gap-0.5"
          role="img"
          :aria-label="`${strip.length} jalan terakhir ${automation.label}`"
        >
          <div
            v-for="(r, i) in strip"
            :key="r.id ?? i"
            class="h-full max-w-2 flex-1 rounded-sm"
            :class="stateOf(r).bar"
            :title="`${formatDateTime(r.started_at)} · ${stateOf(r).label}${r.deployment ? ` · ${r.deployment}` : ''}`"
          />
        </div>
        <p class="mt-1 text-xs text-muted">
          {{ automation.history.length }} jalan dalam 90 hari terakhir
        </p>
      </div>
      <p
        v-else
        class="text-sm text-muted"
      >
        Belum ada jalan dalam 90 hari terakhir.
      </p>
    </template>
  </UCard>
</template>
