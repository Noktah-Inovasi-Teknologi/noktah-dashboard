<script setup lang="ts">
import type { TabsItem } from '@nuxt/ui'
import type { AiUsage, Card, CardDefinition, ClientRecord, Intake, IntakeListItem, Proposal } from '~/types/hub'

/**
 * Intake (US2): paste a chat, a screenshot, a Google Doc or a PDF for ONE Client;
 * AI drafts Proposals, deterministic checks flag them, and a Manager decides each
 * one. The page opens on the Client's latest Intake, so undecided Proposals are
 * still here after leaving and coming back.
 */
const route = useRoute()
const router = useRouter()
const toast = useToast()
const id = computed(() => String(route.params.id))
const { data: me } = await useMe()
const { data: definition } = await useCardDefinition()
const { data: client, error: clientError } = await useFetch<ClientRecord>(() => `/api/hub/v1/clients/${id.value}`)
const { data: card, refresh: refreshCard } = await useFetch<Card>(() => `/api/hub/v1/clients/${id.value}/card`)
const { data: usage, refresh: refreshUsage } = await useFetch<AiUsage>('/api/hub/v1/ai/usage')
const { data: past, refresh: refreshPast } = await useFetch<IntakeListItem[]>(
  () => `/api/hub/v1/clients/${id.value}/intakes`, { default: () => [] })

const selectedId = computed(() => (route.query.intake ? String(route.query.intake) : past.value[0]?.id) ?? null)
const { data: intake, error: intakeError, refresh: refreshIntake } = await useFetch<Intake>(
  () => `/api/hub/v1/intakes/${selectedId.value}`, { immediate: !!selectedId.value, watch: [selectedId] })
const shown = computed(() => (selectedId.value ? intake.value : null))
useSeoMeta({ title: () => (client.value ? `Intake · ${client.value.name} · Noktah Hub` : 'Intake · Noktah Hub') })

// ── input ────────────────────────────────────────────────────────────────────
type Kind = 'text' | 'image' | 'gdoc' | 'pdf'
const kind = ref<Kind>('text')
const TABS: TabsItem[] = [
  { label: 'Teks', value: 'text', icon: 'i-lucide-message-square-text' },
  { label: 'Screenshot', value: 'image', icon: 'i-lucide-image' },
  { label: 'Google Doc', value: 'gdoc', icon: 'i-lucide-file-text' },
  { label: 'PDF', value: 'pdf', icon: 'i-lucide-file-type' }
]
const text = ref('')
const url = ref('')
const image = ref<File | null>(null)
const pdf = ref<File | null>(null)
const LIMIT = { image: 5, pdf: 7 }
const ready = computed(() => ({
  text: text.value.trim().length > 0,
  image: !!image.value,
  gdoc: /docs\.google\.com\/document\/d\//.test(url.value),
  pdf: !!pdf.value
})[kind.value])
const paused = computed(() => usage.value?.paused ?? false)
const processing = ref(false)

function toBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',', 2)[1] ?? '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

async function body(): Promise<Record<string, unknown>> {
  if (kind.value === 'text') return { kind: 'text', text: text.value }
  if (kind.value === 'gdoc') return { kind: 'gdoc', url: url.value.trim() }
  const file = kind.value === 'image' ? image.value! : pdf.value!
  const limit = LIMIT[kind.value]
  if (file.size > limit * 1024 * 1024) throw new Error(`Berkas terlalu besar (maks ${limit} MB).`)
  const b64 = await toBase64(file)
  return kind.value === 'image'
    ? { kind: 'image', image_base64: b64, mime: file.type, filename: file.name }
    : { kind: 'pdf', pdf_base64: b64, filename: file.name }
}

async function submit() {
  processing.value = true
  try {
    const res = await $fetch<Intake>(`/api/hub/v1/clients/${id.value}/intakes`, { method: 'POST', body: await body() })
    if (res.cached) toast.add({ color: 'info', title: 'Sudah pernah diproses', description: 'Menampilkan hasil sebelumnya.' })
    const skipped = (res.dropped?.patient_data ?? 0) + (res.dropped?.invalid ?? 0)
    if (res.dropped?.patient_data) {
      toast.add({ color: 'neutral', title: `${res.dropped.patient_data} item dilewati`, description: 'Berisi data pasien/pelanggan, tidak pernah disimpan.' })
    } else if (skipped) {
      toast.add({ color: 'neutral', title: `${skipped} item dilewati`, description: 'Tidak cocok dengan kolom kartu.' })
    }
    text.value = ''
    url.value = ''
    image.value = null
    pdf.value = null
    await router.replace({ query: { ...route.query, intake: res.id } })
    await Promise.all([refreshPast(), refreshUsage()])
    intake.value = res
  } catch (err) {
    const e = err instanceof Error && !('data' in err) ? { message: err.message, code: 'invalid' } : hubError(err)
    toast.add({ color: 'error', title: 'Gagal memproses', description: e.message })
    if (e.code === 'ai_cap_reached') await refreshUsage()
  } finally {
    processing.value = false
  }
}

// ── results ──────────────────────────────────────────────────────────────────
function onDecided(res: { proposal: Proposal, card?: { card_version: number } }) {
  if (!intake.value) return
  intake.value = { ...intake.value, proposals: intake.value.proposals.map(p => (p.id === res.proposal.id ? res.proposal : p)) }
  if (res.card && card.value) card.value = { ...card.value, card_version: res.card.card_version }
  else refreshCard()
  refreshPast()
}
const pendingCount = computed(() => shown.value?.proposals.filter(p => p.outcome === 'pending').length ?? 0)
const KIND_LABEL: Record<string, string> = { text: 'Teks', image: 'Screenshot', gdoc: 'Google Doc', pdf_text: 'PDF', pdf_scanned: 'PDF (scan)', old_note: 'Catatan lama' }
const FAILURE: Record<string, string> = {
  validation_failed: 'Jawaban AI tidak valid dua kali.',
  truncated: 'Jawaban AI terpotong.',
  timeout: 'AI terlalu lama menjawab.',
  provider_error: 'Layanan AI sedang bermasalah.',
  cap_reached: 'Batas biaya AI bulan ini tercapai.',
  unreadable: 'Sumber tidak bisa dibaca.'
}
const pastItems = computed(() => past.value.map(i => ({
  label: `${formatDate(i.submitted_at)} · ${KIND_LABEL[i.kind] ?? i.kind}${i.pending ? ` · ${i.pending} menunggu` : ''}`,
  value: i.id
})))
const pickPast = (value: string) => router.replace({ query: { ...route.query, intake: value } })
</script>

<template>
  <UDashboardPanel id="intake">
    <template #header>
      <UDashboardNavbar :title="client ? `Intake · ${client.name}` : 'Intake'">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            :to="`/clients/${id}`"
            color="neutral"
            variant="ghost"
            icon="i-lucide-arrow-left"
            label="Kartu"
          />
        </template>
      </UDashboardNavbar>
    </template>

    <template #body>
      <UAlert
        v-if="clientError"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(clientError).message"
      />
      <div
        v-else-if="client && definition"
        class="space-y-6"
      >
        <UCard :ui="{ body: 'space-y-4' }">
          <template #header>
            <div class="flex flex-wrap items-center gap-x-4 gap-y-2">
              <h2 class="font-medium text-highlighted">
                Masukkan bahan
              </h2>
              <div
                v-if="usage"
                class="ms-auto flex items-center gap-2 text-xs text-muted w-full sm:w-64"
              >
                <span class="shrink-0">AI bulan ini</span>
                <UProgress
                  :model-value="Math.min(usage.spent_usd, usage.cap_usd)"
                  :max="usage.cap_usd"
                  size="xs"
                  :color="paused ? 'error' : usage.spent_usd >= usage.cap_usd * 0.8 ? 'warning' : 'primary'"
                  class="min-w-0 flex-1"
                />
                <span class="shrink-0 tabular-nums">${{ usage.spent_usd.toFixed(2) }} / ${{ usage.cap_usd.toFixed(0) }}</span>
              </div>
            </div>
          </template>

          <UAlert
            v-if="paused"
            color="warning"
            variant="subtle"
            icon="i-lucide-pause"
            title="Intake dijeda: batas biaya AI bulan ini sudah tercapai."
            description="Isian kartu tetap bisa diubah langsung di tab Profil dan Guideline."
          />

          <UTabs
            v-model="kind"
            :items="TABS"
            :content="false"
            variant="link"
            :ui="{ list: 'w-max min-w-full', trigger: 'shrink-0' }"
            class="w-full overflow-x-auto items-start"
          />
          <div>
            <template v-if="kind === 'text'">
              <UTextarea
                v-model="text"
                :rows="8"
                autoresize
                :maxrows="20"
                placeholder="Tempel chat WhatsApp atau catatan rapat di sini, apa adanya."
                class="w-full"
                aria-label="Teks"
              />
            </template>
            <template v-else-if="kind === 'image'">
              <UFileUpload
                v-model="image"
                accept="image/png,image/jpeg,image/webp"
                label="Pilih atau tarik screenshot"
                description="PNG, JPEG, atau WebP, maks 5 MB. Hasilnya ditandai “cek manual”."
                class="w-full min-h-40"
              />
            </template>
            <template v-else-if="kind === 'gdoc'">
              <UFormField
                label="Tautan Google Doc"
                help="Dokumen harus bisa dibuka akun Noktah."
              >
                <UInput
                  v-model="url"
                  type="url"
                  placeholder="https://docs.google.com/document/d/…"
                  class="w-full"
                />
              </UFormField>
            </template>
            <template v-else>
              <UFileUpload
                v-model="pdf"
                accept="application/pdf"
                label="Pilih atau tarik PDF"
                description="Maks 7 MB. PDF hasil scan dibaca sebagai gambar (maks 10 halaman)."
                class="w-full min-h-40"
              />
            </template>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <UButton
              icon="i-lucide-sparkles"
              label="Proses"
              :loading="processing"
              :disabled="!ready || paused || processing"
              @click="submit"
            />
            <span
              v-if="processing"
              class="text-sm text-muted"
            >AI sedang membaca… bisa sampai satu menit.</span>
          </div>
        </UCard>

        <section class="space-y-4">
          <div class="flex flex-wrap items-center gap-2">
            <h2 class="font-medium text-highlighted">
              Usulan
            </h2>
            <UBadge
              v-if="pendingCount"
              :label="`${pendingCount} menunggu keputusan`"
              color="warning"
              variant="subtle"
            />
            <USelect
              v-if="pastItems.length > 1"
              :model-value="selectedId ?? undefined"
              :items="pastItems"
              class="ms-auto w-full sm:w-72"
              aria-label="Intake sebelumnya"
              @update:model-value="v => pickPast(String(v))"
            />
          </div>

          <UAlert
            v-if="intakeError"
            color="error"
            icon="i-lucide-server-off"
            :title="hubError(intakeError).message"
          />
          <UEmpty
            v-else-if="!shown"
            icon="i-lucide-clipboard-paste"
            title="Belum ada Intake untuk klien ini"
            description="Tempel chat, screenshot, atau dokumen di atas."
          />
          <template v-else>
            <p class="text-xs text-muted">
              {{ KIND_LABEL[shown.kind] ?? shown.kind }} · dikirim {{ shown.submitted_by ?? 'sistem' }}, {{ formatDate(shown.submitted_at) }}
              <template v-if="!shown.raw_available">
                · bahan mentah sudah dihapus (lebih dari 12 bulan)
              </template>
            </p>
            <UAlert
              v-if="shown.status === 'failed'"
              color="error"
              variant="subtle"
              icon="i-lucide-circle-alert"
              title="AI gagal memproses — coba lagi"
              :description="FAILURE[shown.failure_reason ?? ''] ?? shown.failure_reason ?? undefined"
            />
            <UAlert
              v-else-if="shown.status === 'processing'"
              color="info"
              variant="subtle"
              icon="i-lucide-loader"
              title="Masih diproses…"
              :actions="[{ label: 'Muat ulang', onClick: () => { refreshIntake() } }]"
            />
            <UEmpty
              v-else-if="!shown.proposals.length"
              icon="i-lucide-circle-check"
              title="Tidak ada yang perlu diperbarui"
              description="Tidak ada fakta atau permintaan baru di bahan ini."
            />
            <ul
              v-else
              class="space-y-4"
            >
              <li
                v-for="p in shown.proposals"
                :key="p.id"
              >
                <IntakeProposalCard
                  :proposal="p"
                  :kind="shown.kind"
                  :definition="definition as CardDefinition"
                  :card-version="card?.card_version ?? 0"
                  :can-approve="me?.can.approve ?? false"
                  @decided="onDecided"
                />
              </li>
            </ul>
            <div
              v-if="shown.no_card_home.length"
              class="text-sm"
            >
              <h3 class="text-xs text-muted mb-1">
                Tidak masuk kartu
              </h3>
              <ul class="list-disc ps-5 space-y-1 text-muted">
                <li
                  v-for="(n, i) in shown.no_card_home"
                  :key="i"
                >
                  {{ n }}
                </li>
              </ul>
            </div>
          </template>
        </section>
      </div>
    </template>
  </UDashboardPanel>
</template>
