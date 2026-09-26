<script setup lang="ts">
import type { IncentivePerson, IncentiveReport, IncentiveSanction, SanctionLevel } from '~/types/hub'

/**
 * Laporan → Insentif (US8, US9): per person, the month's Violation and Excellence points
 * (never netted, FR-078), the sanction the ladder gives, the team reward, and the Events
 * behind them. A computed sanction is issued at the end of working day 3 unless someone holds
 * it with a reason (ADR-0001). Sanctions issued outside the Hub are recorded here (FR-079).
 * Needs "Lihat poin insentif". No rupiah amounts anywhere.
 */
const route = useRoute()
const router = useRouter()
const toast = useToast()
const { data: me, error: meError } = await useMe()
useSeoMeta({ title: 'Insentif · Noktah Hub' })

const month = computed({
  get: () => monthFromQuery(route.query.month, monthKey(-1)),
  set: (value: string) => router.replace({ query: { ...route.query, month: value } })
})
const { data, error, status, refresh } = await useFetch<IncentiveReport>('/api/hub/v1/reports/incentive', {
  query: { month },
  immediate: !!me.value?.can.view_incentive
})

const people = computed(() => data.value?.people ?? [])
const { page, pageRows, pageSize, total } = usePaged(people)
const sanctionsOf = (p: IncentivePerson) => p.sanctions ?? (p.sanction ? [p.sanction] : [])
const withSanction = computed(() => people.value.filter(p => sanctionsOf(p).length).length)

// Hold only a computed sanction before its hold window closes; release only a held one (ADR-0001).
const canHold = (s: IncentiveSanction) => s.state === 'computed' && !!s.hold_until && new Date(s.hold_until) > new Date()
const canRelease = (s: IncentiveSanction) => s.state === 'held'
const sanctionText = (s: IncentiveSanction) =>
  `${SANCTION_LEVEL[s.level] ?? s.level_label ?? s.level}${s.with_pip ? ' + PIP 30 hari' : ''}`
const SOURCE: Record<string, string> = { recorded: 'dicatat manual', ladder: 'dari poin bulanan', direct: 'sanksi langsung' }
const EVENT_TYPE: Record<string, string> = { violation: 'Violation', excellence: 'Excellence' }
const eventType = (t: string | null) => (t ? EVENT_TYPE[t.toLowerCase()] ?? t : 'Jenis belum diisi')

// ── hold / release ──────────────────────────────────────────────────────────────────
const holdFor = ref<{ person: IncentivePerson, sanction: IncentiveSanction } | null>(null)
const holdReason = ref('')
const busy = ref(false)
function openHold(person: IncentivePerson, sanction: IncentiveSanction) {
  holdFor.value = { person, sanction }
  holdReason.value = ''
}
async function act(path: string, body: Record<string, unknown> | undefined, done: string) {
  busy.value = true
  try {
    await $fetch(`/api/hub/v1/sanctions/${path}`, { method: 'POST', body })
    toast.add({ color: 'success', title: done })
    await refresh()
    return true
  } catch (err) {
    const e = hubError(err)
    toast.add({ color: e.status === 409 ? 'warning' : 'error', title: 'Gagal', description: e.message })
    if (e.status === 409) await refresh()
    return false
  } finally {
    busy.value = false
  }
}
async function hold() {
  const s = holdFor.value?.sanction
  if (!s) return
  if (await act(`${s.id}/hold`, { reason: holdReason.value.trim() }, 'Sanksi ditahan')) holdFor.value = null
}
const holdOpen = computed({
  get: () => !!holdFor.value,
  set: (v: boolean) => {
    if (!v) holdFor.value = null
  }
})

// ── "Catat sanksi": a teguran lisan or SP issued outside the Hub (FR-079) ───────────
const LEVELS: { label: string, value: SanctionLevel }[] = [
  { label: 'Teguran lisan', value: 'teguran_lisan' },
  { label: 'SP1', value: 'sp1' },
  { label: 'SP2', value: 'sp2' },
  { label: 'SP3', value: 'sp3' },
  { label: 'Peringatan Pertama dan Terakhir', value: 'peringatan_terakhir' }
]
const recordOpen = ref(false)
const draft = reactive({ person_id: '', level: 'teguran_lisan' as SanctionLevel, issued_on: '', category_code: '', note: '' })
const personItems = computed(() => people.value.map(p => ({ label: p.person.name, value: p.person.id })))
watch(recordOpen, (open) => {
  if (open) Object.assign(draft, { person_id: '', level: 'teguran_lisan', issued_on: new Date().toISOString().slice(0, 10), category_code: '', note: '' })
})
async function record() {
  busy.value = true
  try {
    await $fetch('/api/hub/v1/sanctions', {
      method: 'POST',
      body: { person_id: draft.person_id, level: draft.level, issued_on: draft.issued_on, category_code: draft.category_code.trim(), note: draft.note.trim() || null }
    })
    recordOpen.value = false
    toast.add({ color: 'success', title: 'Sanksi dicatat', description: 'Berlaku 90 hari sejak tanggal terbit.' })
    await refresh()
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <UDashboardPanel id="incentive">
    <template #header>
      <UDashboardNavbar title="Insentif">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            v-if="me?.can.view_reports"
            :to="`/reports?month=${month}`"
            color="neutral"
            variant="ghost"
            icon="i-lucide-arrow-left"
            label="Laporan"
          />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UAlert
        v-if="meError"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(meError).message"
      />
      <UAlert
        v-else-if="!me?.can.view_incentive"
        color="neutral"
        variant="subtle"
        icon="i-lucide-lock"
        title="Halaman ini butuh izin Lihat poin insentif."
      />
      <UAlert
        v-else-if="error"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(error).message"
      />
      <div
        v-else-if="data"
        class="space-y-4"
      >
        <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
          <MonthSelect
            v-model="month"
            :to="monthKey(0)"
          />
          <p class="flex items-center gap-1.5 text-sm text-muted sm:me-auto">
            <UIcon
              name="i-lucide-history"
              class="size-4 shrink-0"
            />
            {{ refreshedLine(data.refreshed_at) }}
          </p>
          <UButton
            icon="i-lucide-file-pen-line"
            label="Catat sanksi"
            class="justify-center"
            :disabled="!people.length"
            @click="recordOpen = true"
          />
        </div>
        <p class="max-w-3xl text-sm text-muted">
          Poin dari Event yang sudah dinilai di Jira, sesuai Incentive Framework v2.1. Poin Violation dan Excellence ditampilkan terpisah, tidak dijumlahkan. Sanksi yang dihitung terbit otomatis di akhir hari kerja ke-3 kecuali ditahan dengan alasan.
        </p>

        <UAlert
          v-if="data.direct_sanctions_manual.length"
          color="error"
          variant="subtle"
          icon="i-lucide-shield-alert"
          title="Sanksi langsung yang perlu ditentukan manual"
        >
          <template #description>
            <p class="mb-1">
              Event H2 yang kolom Direct Sanction Violation-nya kosong tidak diproses otomatis. Isi kolom itu di Jira, atau catat sanksinya lewat Catat sanksi.
            </p>
            <ul class="space-y-1">
              <li
                v-for="d in data.direct_sanctions_manual"
                :key="d.key"
              >
                <ULink
                  :to="`${JIRA_BROWSE}${d.key}`"
                  target="_blank"
                  class="font-medium underline"
                >
                  {{ d.key }}
                </ULink>
                · {{ d.person ?? 'Person belum diisi' }}<template v-if="d.summary">
                  · {{ d.summary }}
                </template>
              </li>
            </ul>
          </template>
        </UAlert>
        <UAlert
          v-if="data.incomplete.length"
          color="warning"
          variant="subtle"
          icon="i-lucide-list-checks"
          title="Event belum lengkap"
        >
          <template #description>
            <p class="mb-1">
              Event ini tidak dihitung sampai isiannya lengkap di Jira. Hub tidak menebak nilainya.
            </p>
            <ul class="space-y-1">
              <li
                v-for="e in data.incomplete"
                :key="e.key"
                class="wrap-break-word"
              >
                <ULink
                  :to="`${JIRA_BROWSE}${e.key}`"
                  target="_blank"
                  class="font-medium underline"
                >
                  {{ e.key }}
                </ULink>
                <template v-if="e.person">
                  · {{ e.person }}
                </template>
                · kurang {{ e.missing.join(', ') }}
              </li>
            </ul>
          </template>
        </UAlert>
        <UAlert
          v-if="data.unmatched?.length"
          color="neutral"
          variant="subtle"
          icon="i-lucide-user-x"
          title="Event untuk orang yang belum ada di Hub"
        >
          <template #description>
            <p class="mb-1">
              Person atau Assignee Event ini tidak cocok dengan Jira account ID siapa pun di halaman Orang. Isi Jira account ID orangnya agar poinnya terhitung.
            </p>
            <ul class="space-y-1">
              <li
                v-for="e in data.unmatched"
                :key="e.key"
                class="break-all"
              >
                <ULink
                  :to="`${JIRA_BROWSE}${e.key}`"
                  target="_blank"
                  class="font-medium underline"
                >
                  {{ e.key }}
                </ULink>
                <template v-if="e.account_id">
                  · {{ e.account_id }}
                </template>
              </li>
            </ul>
          </template>
        </UAlert>

        <UEmpty
          v-if="!people.length && status !== 'pending'"
          icon="i-lucide-scale"
          :title="`Belum ada Event yang dinilai di ${monthLabel(month)}`"
          description="Poin muncul di sini setelah Event di Jira dinilai."
        />
        <template v-else>
          <p class="text-sm text-muted">
            {{ people.length }} orang · {{ withSanction }} dengan sanksi
          </p>
          <ul class="space-y-3">
            <li
              v-for="p in pageRows"
              :key="p.person.id"
            >
              <UCard :ui="{ body: 'space-y-3' }">
                <div class="flex flex-wrap items-center gap-2">
                  <NuxtLink
                    :to="`/people/${p.person.id}`"
                    class="me-auto font-medium text-highlighted hover:underline"
                  >
                    {{ p.person.name }}
                  </NuxtLink>
                  <UBadge
                    :color="p.violation_points ? 'error' : 'neutral'"
                    variant="subtle"
                    :label="`Violation ${formatPoints(p.violation_points)}`"
                  />
                  <UBadge
                    :color="p.excellence_points ? 'success' : 'neutral'"
                    variant="subtle"
                    :label="`Excellence ${formatPoints(p.excellence_points)}`"
                  />
                </div>

                <div
                  v-for="s in sanctionsOf(p)"
                  :key="s.id"
                  class="flex flex-col gap-2 rounded-md bg-elevated p-3 sm:flex-row sm:items-center"
                >
                  <div class="min-w-0 flex-1 space-y-1">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="font-medium text-highlighted">{{ sanctionText(s) }}</span>
                      <UBadge
                        :color="sanctionState(s.state).color"
                        variant="subtle"
                        size="sm"
                        :label="sanctionState(s.state).label"
                      />
                      <span
                        v-if="s.source && SOURCE[s.source]"
                        class="text-xs text-muted"
                      >{{ SOURCE[s.source] }}</span>
                    </div>
                    <p
                      v-if="s.hold_until && s.state === 'computed'"
                      class="text-xs text-muted"
                    >
                      Batas tahan: {{ formatDateTime(s.hold_until) }}
                    </p>
                    <p
                      v-if="s.hold_reason"
                      class="text-xs text-muted wrap-break-word"
                    >
                      Alasan ditahan: {{ s.hold_reason }}
                    </p>
                    <p
                      v-if="s.issued_on"
                      class="text-xs text-muted"
                    >
                      {{ `Terbit ${formatDate(s.issued_on)}${s.valid_until ? `, berlaku sampai ${formatDate(s.valid_until)}` : ''}` }}
                    </p>
                    <p
                      v-if="s.letter_number"
                      class="text-xs text-muted"
                    >
                      Surat
                      <ULink
                        v-if="s.letter_url"
                        :to="s.letter_url"
                        target="_blank"
                        class="text-primary hover:underline"
                      >
                        {{ s.letter_number }}
                      </ULink>
                      <template v-else>
                        {{ s.letter_number }}
                      </template>
                    </p>
                    <p
                      v-if="s.level === 'phk_flag'"
                      class="text-xs text-muted"
                    >
                      Hub hanya menandai; prosesnya dimulai oleh manajer.
                    </p>
                  </div>
                  <UButton
                    v-if="canHold(s)"
                    color="warning"
                    variant="outline"
                    icon="i-lucide-hand"
                    label="Tahan"
                    class="justify-center"
                    @click="openHold(p, s)"
                  />
                  <UButton
                    v-else-if="canRelease(s)"
                    color="neutral"
                    variant="outline"
                    icon="i-lucide-undo-2"
                    label="Batalkan penahanan"
                    class="justify-center"
                    :loading="busy"
                    @click="act(`${s.id}/release`, undefined, 'Penahanan dibatalkan')"
                  />
                </div>
                <p
                  v-if="!sanctionsOf(p).length"
                  class="text-sm text-muted"
                >
                  Tidak ada sanksi bulan ini.
                </p>

                <p
                  v-if="p.team_reward"
                  class="text-sm"
                >
                  <UIcon
                    :name="p.team_reward.qualifies ? 'i-lucide-trophy' : 'i-lucide-users-round'"
                    class="me-1 size-4 align-text-bottom text-muted"
                  />
                  Reward tim: {{ p.team_reward.qualifies ? 'memenuhi' : 'belum memenuhi' }}
                  <span class="text-muted">({{ p.team_reward.teams_met }} dari {{ p.team_reward.teams }} tim klien mencapai kedua ambang; minimal 60%)</span>
                </p>

                <UCollapsible v-if="p.events.length">
                  <UButton
                    color="neutral"
                    variant="ghost"
                    size="sm"
                    icon="i-lucide-chevron-right"
                    :label="`${p.events.length} Event`"
                    class="-ms-2"
                  />
                  <template #content>
                    <ul class="mt-2 divide-y divide-default">
                      <li
                        v-for="e in p.events"
                        :key="e.key"
                        class="flex flex-wrap items-center gap-x-2 gap-y-1 py-2 text-sm"
                      >
                        <ULink
                          :to="`${JIRA_BROWSE}${e.key}`"
                          target="_blank"
                          class="font-medium text-primary hover:underline"
                        >
                          {{ e.key }}
                        </ULink>
                        <span class="text-muted">{{ eventType(e.type) }}</span>
                        <span class="min-w-0 wrap-break-word">{{ e.category ?? '—' }}</span>
                        <span
                          class="font-medium tabular-nums"
                          :class="(e.points ?? 0) < 0 ? 'text-error' : (e.points ?? 0) > 0 ? 'text-success' : 'text-muted'"
                        >{{ formatPoints(e.points) }}</span>
                        <UBadge
                          :color="eventState(e.state).color"
                          variant="subtle"
                          size="sm"
                          :label="eventState(e.state).label"
                        />
                        <span
                          v-if="e.summary"
                          class="w-full wrap-break-word"
                        >{{ e.summary }}</span>
                        <span
                          v-if="e.state === 'belum_lengkap' && e.missing?.length"
                          class="w-full text-xs text-muted"
                        >Kurang: {{ e.missing.join(', ') }}</span>
                        <span
                          v-else-if="e.reason"
                          class="w-full text-xs text-muted wrap-break-word"
                        >{{ e.reason }}</span>
                      </li>
                    </ul>
                  </template>
                </UCollapsible>
              </UCard>
            </li>
          </ul>
          <ListPager
            v-model:page="page"
            :total="total"
            :page-size="pageSize"
          />
        </template>
      </div>

      <UModal
        v-model:open="holdOpen"
        :title="holdFor ? `Tahan ${sanctionText(holdFor.sanction)} untuk ${holdFor.person.person.name}?` : 'Tahan sanksi'"
        description="Sanksi yang ditahan tidak diterbitkan. Tulis alasannya; alasan ini tersimpan di riwayat sanksi."
      >
        <template #body>
          <UFormField
            label="Alasan"
            required
          >
            <UTextarea
              v-model="holdReason"
              autoresize
              :rows="3"
              class="w-full"
            />
          </UFormField>
        </template>
        <template #footer>
          <div class="flex w-full justify-end gap-2">
            <UButton
              color="neutral"
              variant="ghost"
              label="Batal"
              @click="holdFor = null"
            />
            <UButton
              color="warning"
              label="Tahan"
              :loading="busy"
              :disabled="!holdReason.trim()"
              @click="hold"
            />
          </div>
        </template>
      </UModal>

      <UModal
        v-model:open="recordOpen"
        title="Catat sanksi"
        description="Untuk teguran lisan atau SP yang diberikan di luar Hub. Dihitung di tangga sanksi selama 90 hari sejak tanggal terbit."
        :ui="{ content: 'sm:max-w-xl' }"
      >
        <template #body>
          <div class="space-y-4">
            <UFormField
              label="Orang"
              required
            >
              <USelect
                v-model="draft.person_id"
                :items="personItems"
                placeholder="Pilih orang…"
                class="w-full"
              />
            </UFormField>
            <div class="grid gap-3 sm:grid-cols-2">
              <UFormField
                label="Tingkat"
                required
              >
                <USelect
                  v-model="draft.level"
                  :items="LEVELS"
                  class="w-full"
                />
              </UFormField>
              <UFormField
                label="Tanggal terbit"
                required
              >
                <UInput
                  v-model="draft.issued_on"
                  type="date"
                  class="w-full"
                />
              </UFormField>
            </div>
            <UFormField
              label="Kode kategori"
              help="Kode kategori Violation, mis. C2. Tiga teguran lisan berkode sama dalam 90 hari menjadi SP1."
              required
            >
              <UInput
                v-model="draft.category_code"
                class="w-full"
              />
            </UFormField>
            <UFormField label="Catatan">
              <UTextarea
                v-model="draft.note"
                autoresize
                :rows="2"
                class="w-full"
              />
            </UFormField>
          </div>
        </template>
        <template #footer>
          <div class="flex w-full justify-end gap-2">
            <UButton
              color="neutral"
              variant="ghost"
              label="Batal"
              @click="recordOpen = false"
            />
            <UButton
              label="Simpan"
              :loading="busy"
              :disabled="!draft.person_id || !draft.issued_on || !draft.category_code.trim()"
              @click="record"
            />
          </div>
        </template>
      </UModal>
    </template>
  </UDashboardPanel>
</template>
