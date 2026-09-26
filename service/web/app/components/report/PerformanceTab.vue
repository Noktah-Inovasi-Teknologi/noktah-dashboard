<script setup lang="ts">
import type { TableColumn } from '@nuxt/ui'
import type { MetricSummary, PerfAccount, PerfPost, PerformanceList, PerformanceReport, PerfSide, RateSummary } from '~/types/hub'

/**
 * Laporan → Performa (US7, FR-060…FR-062): a Client's own accounts beside its competitors'
 * average for the month. Every average says how many posts it rests on; a figure that can't
 * be known says why instead of showing a blank (FR-061). Marked posts are left out of the
 * averages and top posts, and listed on their own (FR-035).
 */
const props = defineProps<{ month: string }>()
const route = useRoute()
const router = useRouter()

const { data: list, error: listError } = await useFetch<PerformanceList>('/api/hub/v1/reports/performance', {
  query: { month: toRef(props, 'month') }
})
const clientItems = computed(() => (list.value?.clients ?? []).map(c => ({ label: c.client.name, value: c.client.id })))
const clientId = computed({
  get: () => {
    const wanted = String(route.query.client ?? '')
    return clientItems.value.some(c => c.value === wanted) ? wanted : (clientItems.value[0]?.value ?? '')
  },
  set: (value: string) => router.replace({ query: { ...route.query, client: value } })
})
const { data, error, status } = await useFetch<PerformanceReport>('/api/hub/v1/reports/performance', {
  query: { month: toRef(props, 'month'), client_id: clientId },
  immediate: !!clientId.value,
  watch: [clientId, toRef(props, 'month')]
})
const shown = computed(() => (clientId.value && data.value?.client?.id === clientId.value ? data.value : null))

const account = (a: PerfAccount) => typeof a === 'string' ? a : `@${a.handle}`
const accountIcon = (a: PerfAccount) => typeof a === 'string' ? 'i-lucide-at-sign' : (PLATFORM_ICONS[a.platform] ?? 'i-lucide-at-sign')

// One row per figure, own beside the competitors' average.
interface Line { key: string, label: string, own: string, ownNote: string, comp: string, compNote: string, change: number | null }
const METRICS: { key: 'views' | 'likes' | 'comments' | 'shares', label: string }[] = [
  { key: 'views', label: 'Views' }, { key: 'likes', label: 'Likes' }, { key: 'comments', label: 'Komentar' }, { key: 'shares', label: 'Share' }
]
const avgText = (m: MetricSummary | undefined) => (m && m.n ? formatCount(m.average, 1) : '—')
const avgNote = (m: MetricSummary | undefined) => (m
  ? `total ${formatCount(m.total, 1)} · rata-rata dari ${m.n} post${m.accounts ? `, ${m.accounts} akun` : ''}`
  : '')
const rateText = (r: RateSummary | undefined) => (r?.unavailable ? r.unavailable : formatPercent(r?.value ?? null, 2))
const rateNote = (r: RateSummary | undefined) => {
  if (!r || r.unavailable || r.n === undefined) return ''
  return `dari ${r.n} post${r.followers ? `, ${formatCount(r.followers)} pengikut` : ''}`
}
const types = (s: Partial<PerfSide> | undefined) => Object.entries(s?.posts_by_type ?? {})
  .map(([t, n]) => `${formatCount(n, 1)} ${contentType(t).toLowerCase()}`).join(', ')

const lines = computed<Line[]>(() => {
  const r = shown.value
  if (!r) return []
  const own = r.own
  const comp = r.competitors?.average
  const change = (k: string) => own.change?.[k] ?? null
  return [
    { key: 'posts', label: 'Post', own: formatCount(own.posts),
      ownNote: [types(own), own.posts_counted !== undefined && own.posts_counted !== own.posts ? `${own.posts_counted} dihitung, sisanya bertanda` : ''].filter(Boolean).join(' · '), comp: comp ? formatCount(comp.posts ?? null, 1) : '—', compNote: types(comp), change: change('posts') },
    ...METRICS.map(m => ({
      key: m.key, label: `${m.label} per post`,
      own: avgText(own[m.key]), ownNote: avgNote(own[m.key]),
      comp: avgText(comp?.[m.key]), compNote: avgNote(comp?.[m.key]),
      change: change(`${m.key}_total`) ?? change(m.key)
    })),
    { key: 'er_views', label: 'Engagement ÷ views (video)', own: rateText(own.engagement_rate_views), ownNote: rateNote(own.engagement_rate_views), comp: rateText(comp?.engagement_rate_views), compNote: rateNote(comp?.engagement_rate_views), change: change('engagement_rate_views') },
    { key: 'er_followers', label: 'Engagement ÷ pengikut', own: rateText(own.engagement_rate_followers), ownNote: rateNote(own.engagement_rate_followers), comp: rateText(comp?.engagement_rate_followers), compNote: rateNote(comp?.engagement_rate_followers), change: change('engagement_rate_followers') }
  ]
})
const columns = computed<TableColumn<Line>[]>(() => [
  { accessorKey: 'label', header: 'Angka', meta: { class: { td: 'whitespace-normal font-medium text-highlighted' } } },
  { id: 'own', header: 'Klien', meta: { class: { td: 'whitespace-normal' } } },
  { id: 'comp', header: 'Pesaing', meta: { class: { td: 'whitespace-normal' } } }
])
const changeText = (v: number) => `${v > 0 ? '+' : v < 0 ? '−' : ''}${formatPercent(Math.abs(v), 0)} dari bulan lalu`

const metrics = (p: PerfPost) => `${formatCount(p.views)} views · ${formatCount(p.likes)} likes · ${formatCount(p.comments)} komentar · ${formatCount(p.shares)} share`
const markedOpen = ref(false)
</script>

<template>
  <div class="space-y-4">
    <UAlert
      v-if="listError"
      color="error"
      icon="i-lucide-server-off"
      :title="hubError(listError).message"
    />
    <template v-else>
      <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
        <USelect
          v-model="clientId"
          :items="clientItems"
          placeholder="Pilih klien…"
          :disabled="!clientItems.length"
          class="w-full sm:w-72"
          aria-label="Klien"
        />
        <p
          v-if="shown?.refreshed_at !== undefined || list?.refreshed_at !== undefined"
          class="flex items-center gap-1.5 text-sm text-muted"
        >
          <UIcon
            name="i-lucide-history"
            class="size-4 shrink-0"
          />
          {{ refreshedLine(shown?.refreshed_at ?? list?.refreshed_at) }}
        </p>
      </div>
      <p class="max-w-3xl text-sm text-muted">
        Dari post hasil Harvest bulan ini. Post yang ditandai (Iklan, Tidak relevan, Bukan konten akun ini) tetap masuk jumlah post, tetapi tidak dihitung di rata-rata dan post teratas.
      </p>

      <UEmpty
        v-if="!clientItems.length"
        icon="i-lucide-chart-column"
        title="Belum ada klien Eskala dengan akun sosial"
        description="Tambahkan akun sosial klien dan pesaingnya di tab Registry klien."
      />
      <UAlert
        v-else-if="error"
        color="error"
        icon="i-lucide-server-off"
        :title="hubError(error).message"
      />
      <template v-else-if="shown">
        <div class="grid gap-4 md:grid-cols-2">
          <UCard>
            <p class="text-sm text-muted">
              Akun klien
            </p>
            <ul class="mt-2 flex flex-wrap gap-x-3 gap-y-1">
              <li
                v-for="a in shown.own.accounts"
                :key="account(a)"
                class="flex min-w-0 items-center gap-1 text-sm"
              >
                <UIcon
                  :name="accountIcon(a)"
                  class="size-4 shrink-0 text-muted"
                />
                <span class="break-all">{{ account(a) }}</span>
              </li>
            </ul>
          </UCard>
          <UCard>
            <p class="text-sm text-muted">
              Pesaing
            </p>
            <ul
              v-if="shown.competitors"
              class="mt-2 flex flex-wrap gap-x-3 gap-y-1"
            >
              <li
                v-for="a in shown.competitors.accounts"
                :key="account(a)"
                class="flex min-w-0 items-center gap-1 text-sm"
              >
                <UIcon
                  :name="accountIcon(a)"
                  class="size-4 shrink-0 text-muted"
                />
                <span class="break-all">{{ account(a) }}</span>
              </li>
            </ul>
            <p
              v-else
              class="mt-2 text-sm text-muted"
            >
              Belum ada akun pesaing di Registry klien ini.
            </p>
          </UCard>
        </div>

        <UCard :ui="{ body: 'p-0 sm:p-0' }">
          <template #header>
            <p class="text-sm text-muted">
              {{ shown.competitors ? `Kolom Pesaing adalah rata-rata per akun dari ${shown.competitors.n_accounts} akun pesaing.` : 'Klien ini belum punya pesaing untuk dibandingkan.' }}
            </p>
          </template>
          <UTable
            :data="lines"
            :columns="columns"
            :loading="status === 'pending'"
          >
            <template #label-cell="{ row }">
              <span class="block min-w-24">{{ row.original.label }}</span>
            </template>
            <template #own-cell="{ row }">
              <span class="block text-highlighted tabular-nums">{{ row.original.own }}</span>
              <span
                v-if="row.original.ownNote"
                class="block text-xs text-muted"
              >{{ row.original.ownNote }}</span>
              <span
                v-if="row.original.change !== null"
                class="block text-xs"
                :class="row.original.change < 0 ? 'text-error' : 'text-success'"
              >{{ changeText(row.original.change) }}</span>
            </template>
            <template #comp-cell="{ row }">
              <span class="block text-highlighted tabular-nums">{{ row.original.comp }}</span>
              <span
                v-if="row.original.compNote"
                class="block text-xs text-muted"
              >{{ row.original.compNote }}</span>
            </template>
          </UTable>
        </UCard>

        <UCard>
          <template #header>
            <h3 class="font-medium text-highlighted">
              5 post teratas
            </h3>
          </template>
          <ol
            v-if="shown.own.top_posts?.length"
            class="space-y-3"
          >
            <li
              v-for="(p, i) in shown.own.top_posts.slice(0, 5)"
              :key="`${p.platform}:${p.content_id}`"
              class="flex items-start gap-3"
            >
              <span class="w-4 shrink-0 text-sm text-muted tabular-nums">{{ i + 1 }}</span>
              <div class="min-w-0 flex-1 space-y-1">
                <div class="flex flex-wrap items-center gap-2 text-sm">
                  <span class="text-highlighted">{{ formatDate(p.published_at) }}</span>
                  <UBadge
                    color="neutral"
                    variant="subtle"
                    size="sm"
                    :label="contentType(p.content_type)"
                  />
                  <ULink
                    v-if="p.url"
                    :to="p.url"
                    target="_blank"
                    class="text-primary hover:underline"
                  >
                    Buka post
                  </ULink>
                </div>
                <p class="line-clamp-2 text-sm text-ellipsis wrap-break-word">
                  {{ p.caption || '(tanpa caption)' }}
                </p>
                <p class="text-xs text-muted tabular-nums">
                  {{ metrics(p) }}
                </p>
              </div>
            </li>
          </ol>
          <p
            v-else
            class="text-sm text-muted"
          >
            Belum ada post yang dihitung bulan ini.
          </p>
        </UCard>

        <UCollapsible
          v-if="shown.own.marked?.length"
          v-model:open="markedOpen"
        >
          <UButton
            color="neutral"
            variant="outline"
            :icon="markedOpen ? 'i-lucide-chevron-down' : 'i-lucide-chevron-right'"
            :label="`Post bertanda, tidak dihitung (${shown.own.marked.length})`"
          />
          <template #content>
            <ul class="mt-3 space-y-3">
              <li
                v-for="p in shown.own.marked"
                :key="`${p.platform}:${p.content_id}`"
                class="space-y-1"
              >
                <div class="flex flex-wrap items-center gap-2 text-sm">
                  <span class="text-highlighted">{{ formatDate(p.published_at) }}</span>
                  <UBadge
                    v-for="m in p.marks ?? []"
                    :key="m"
                    color="warning"
                    variant="subtle"
                    size="sm"
                    :label="markLabel(m)"
                  />
                  <ULink
                    v-if="p.url"
                    :to="p.url"
                    target="_blank"
                    class="text-primary hover:underline"
                  >
                    Buka post
                  </ULink>
                </div>
                <p class="line-clamp-2 text-sm text-ellipsis wrap-break-word">
                  {{ p.caption || '(tanpa caption)' }}
                </p>
                <p class="text-xs text-muted tabular-nums">
                  {{ metrics(p) }}
                </p>
              </li>
            </ul>
          </template>
        </UCollapsible>
      </template>
    </template>
  </div>
</template>
