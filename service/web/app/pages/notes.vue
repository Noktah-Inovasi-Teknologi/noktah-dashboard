<script setup lang="ts">
import type { Intake } from '~/types/hub'

/**
 * Catatan lama (US6): the old AnythingLLM notes, processed into Intakes. Notes whose
 * Client couldn't be matched wait here; a Manager picks the Client (which runs the
 * Intake for it) or discards the note. The Client is never guessed (G-17).
 */
interface UnmatchedNote { id: string, subject: string, text: string, source_name: string }
interface NotesPage {
  progress: { processed: number, matched: number, unmatched: number, remaining: number, discarded?: number }
  notes: UnmatchedNote[]
}

const toast = useToast()
const { data, error, refresh } = await useFetch<NotesPage>('/api/hub/v1/notes/unmatched')
const { data: clients } = await useFetch<{ id: string, name: string, brand_name: string | null }[]>(
  '/api/hub/v1/clients', { query: { status: 'all' }, default: () => [] })
const { data: me } = await useMe()
useSeoMeta({ title: 'Catatan lama · Noktah Hub' })

const clientItems = computed(() => clients.value.map(c => ({
  label: (me.value?.brands.length ?? 0) > 1 && c.brand_name ? `${c.name} · ${c.brand_name}` : c.name, value: c.id
})))
const picked = reactive<Record<string, string | undefined>>({})
const busy = ref<string | null>(null)
const total = computed(() => (data.value ? data.value.progress.processed + data.value.progress.remaining : 0))

async function assign(note: UnmatchedNote) {
  busy.value = note.id
  try {
    const intake = await $fetch<Intake>(`/api/hub/v1/notes/${note.id}/assign`, { method: 'POST', body: { client_id: picked[note.id] } })
    toast.add({
      color: 'success', title: `Ditetapkan ke ${intake.client?.name}`,
      description: intake.proposals.length ? `${intake.proposals.length} usulan menunggu di halaman Intake klien.` : 'Tidak ada yang perlu diperbarui.',
      actions: intake.client ? [{ label: 'Buka', to: `/clients/${intake.client.id}/intake?intake=${intake.id}` }] : undefined
    })
    await refresh()
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    busy.value = null
  }
}

async function discard(note: UnmatchedNote) {
  busy.value = note.id
  try {
    await $fetch(`/api/hub/v1/notes/${note.id}/discard`, { method: 'POST' })
    toast.add({ color: 'neutral', title: 'Catatan dibuang' })
    await refresh()
  } catch (err) {
    toast.add({ color: 'error', title: 'Gagal', description: hubError(err).message })
  } finally {
    busy.value = null
  }
}
</script>

<template>
  <UDashboardPanel id="notes">
    <template #header>
      <UDashboardNavbar title="Catatan lama">
        <template #leading>
          <UDashboardSidebarCollapse />
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
        v-else-if="data"
        class="space-y-6"
      >
        <UCard :ui="{ body: 'space-y-3' }">
          <p class="text-sm">
            Catatan dari AnythingLLM diproses AI menjadi usulan per klien. Usulannya ditinjau di halaman Intake
            masing-masing klien, seperti Intake biasa. Tidak ada yang masuk kartu sebelum diterima.
          </p>
          <UProgress
            :model-value="data.progress.processed"
            :max="total || 1"
            size="sm"
          />
          <dl class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
            <div>
              <dt class="text-muted text-xs">
                Diproses
              </dt>
              <dd class="tabular-nums font-medium">
                {{ data.progress.processed }} / {{ total }}
              </dd>
            </div>
            <div>
              <dt class="text-muted text-xs">
                Cocok dengan klien
              </dt>
              <dd class="tabular-nums font-medium">
                {{ data.progress.matched }}
              </dd>
            </div>
            <div>
              <dt class="text-muted text-xs">
                Belum ada klien
              </dt>
              <dd class="tabular-nums font-medium">
                {{ data.progress.unmatched }}
              </dd>
            </div>
            <div>
              <dt class="text-muted text-xs">
                Belum diproses
              </dt>
              <dd class="tabular-nums font-medium">
                {{ data.progress.remaining }}
              </dd>
            </div>
          </dl>
        </UCard>

        <section class="space-y-3">
          <h2 class="font-medium text-highlighted">
            Belum ada klien
          </h2>
          <UEmpty
            v-if="!data.notes.length"
            icon="i-lucide-circle-check"
            title="Semua catatan sudah punya klien"
          />
          <ul
            v-else
            class="space-y-3"
          >
            <li
              v-for="n in data.notes"
              :key="n.id"
            >
              <UCard :ui="{ body: 'space-y-3' }">
                <div class="flex flex-wrap items-center gap-2">
                  <span class="font-medium">{{ n.subject }}</span>
                  <UBadge
                    :label="`tertulis: ${n.source_name}`"
                    color="neutral"
                    variant="subtle"
                    class="max-w-full whitespace-normal break-words"
                  />
                </div>
                <p class="text-sm whitespace-pre-line break-words">
                  {{ n.text }}
                </p>
                <div class="flex flex-wrap items-center gap-2">
                  <USelectMenu
                    v-model="picked[n.id]"
                    :items="clientItems"
                    value-key="value"
                    placeholder="Pilih klien…"
                    class="w-full sm:w-72"
                    :aria-label="`Klien untuk ${n.subject}`"
                  />
                  <UButton
                    label="Tetapkan"
                    :disabled="!picked[n.id] || busy === n.id"
                    :loading="busy === n.id"
                    @click="assign(n)"
                  />
                  <UButton
                    color="neutral"
                    variant="ghost"
                    label="Buang"
                    :disabled="busy === n.id"
                    @click="discard(n)"
                  />
                </div>
              </UCard>
            </li>
          </ul>
        </section>
      </div>
    </template>
  </UDashboardPanel>
</template>
