<script setup lang="ts">
import type { HarvestPost, HarvestPosts, ReviewMark } from '~/types/hub'

/**
 * One account's harvested posts for a month (US4, FR-034): link, caption, numbers, and the
 * three Review marks. Each change saves the post's full set of marks at once.
 */
const route = useRoute()
const router = useRouter()
const toast = useToast()
const id = computed(() => String(route.params.id))
const month = computed({
  get: () => monthFromQuery(route.query.month, monthKey(-1)),
  set: (value: string) => router.replace({ query: { ...route.query, month: value } })
})
const { data, error, status } = await useFetch<HarvestPosts>(() => `/api/hub/v1/harvest/accounts/${id.value}/posts`, {
  query: { month },
  // Marks are changed in place on each post, so the posts must be deeply reactive.
  deep: true
})
useSeoMeta({ title: () => (data.value ? `@${data.value.account.handle} · Noktah Hub` : 'Post akun · Noktah Hub') })

const posts = computed(() => data.value?.posts ?? [])
const { page, pageRows, pageSize, total } = usePaged(posts)
const markedCount = computed(() => posts.value.filter(p => p.marks.length).length)

const saving = ref<string | null>(null)
async function setMark(post: HarvestPost, mark: ReviewMark, on: boolean | 'indeterminate') {
  const before = [...post.marks]
  const next = on === true ? [...new Set([...before, mark])] : before.filter(m => m !== mark)
  post.marks = REVIEW_MARKS.map(m => m.key).filter(k => next.includes(k))
  saving.value = `${post.platform}:${post.content_id}`
  try {
    const res = await $fetch<{ marks: ReviewMark[] }>(
      `/api/hub/v1/harvest/posts/${post.platform}/${encodeURIComponent(post.content_id)}/marks`,
      { method: 'PUT', body: { marks: post.marks } })
    post.marks = res.marks
  } catch (err) {
    post.marks = before
    toast.add({ color: 'error', title: 'Tanda tidak tersimpan', description: hubError(err).message })
  } finally {
    saving.value = null
  }
}

const account = computed(() => data.value?.account)
const clients = computed(() => account.value?.clients ?? (account.value ? [{ client: account.value.client, role: account.value.role }] : []))
</script>

<template>
  <UDashboardPanel id="account-posts">
    <template #header>
      <UDashboardNavbar :title="account ? `@${account.handle}` : 'Post akun'">
        <template #leading>
          <UDashboardSidebarCollapse />
        </template>
        <template #right>
          <UButton
            to="/automations?tab=harvest"
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
        v-else-if="data && account"
        class="space-y-4"
      >
        <div class="space-y-1">
          <div class="flex min-w-0 flex-wrap items-center gap-2">
            <UIcon
              :name="PLATFORM_ICONS[account.platform] ?? 'i-lucide-at-sign'"
              class="size-5 shrink-0 text-muted"
            />
            <ULink
              :to="account.url"
              target="_blank"
              class="min-w-0 break-all text-lg font-semibold text-highlighted hover:underline"
            >
              @{{ account.handle }}
            </ULink>
            <UBadge
              :color="harvestOutcome(account).color"
              variant="subtle"
              :label="harvestOutcome(account).label"
            />
          </div>
          <p class="text-sm text-muted">
            <template
              v-for="(c, i) in clients"
              :key="c.client.id"
            >
              <template v-if="i">
                ·
              </template>{{ c.client.name }} ({{ ROLE_LABEL[c.role]?.label ?? c.role }})
            </template>
            <template v-if="account.last_harvested_at">
              · Harvest terakhir {{ formatDate(account.last_harvested_at) }}
            </template>
          </p>
        </div>

        <p class="max-w-3xl text-sm text-muted">
          Post yang ditandai tidak dihitung di rata-rata dan post teratas di Laporan Performa, dan tidak dipakai songbird sebagai contoh. Post itu tetap tersimpan dan tetap masuk jumlah post bulan ini. Satu post boleh punya lebih dari satu tanda.
        </p>

        <div class="flex flex-col gap-2 sm:flex-row sm:items-center">
          <MonthSelect
            v-model="month"
            :to="monthKey(0)"
          />
          <p class="text-sm text-muted tabular-nums">
            {{ posts.length }} post · {{ markedCount }} ditandai
          </p>
        </div>

        <UEmpty
          v-if="!posts.length && status !== 'pending'"
          icon="i-lucide-image-off"
          :title="`Belum ada post ${monthLabel(month)}`"
          description="Post muncul di sini setelah Harvest mengumpulkannya."
        />
        <ul
          v-else
          class="space-y-3"
        >
          <li
            v-for="p in pageRows"
            :key="`${p.platform}:${p.content_id}`"
          >
            <UCard :ui="{ body: 'flex flex-col gap-4 lg:flex-row' }">
              <div class="min-w-0 flex-1 space-y-2">
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
                    class="inline-flex items-center gap-1 text-primary hover:underline"
                  >
                    Buka post
                    <UIcon
                      name="i-lucide-external-link"
                      class="size-3.5"
                    />
                  </ULink>
                </div>
                <p
                  class="line-clamp-3 whitespace-normal text-sm text-ellipsis wrap-break-word"
                  :title="p.caption ?? ''"
                >
                  {{ p.caption || '(tanpa caption)' }}
                </p>
                <p class="text-xs text-muted tabular-nums">
                  {{ formatCount(p.views) }} views · {{ formatCount(p.likes) }} likes · {{ formatCount(p.comments) }} komentar · {{ formatCount(p.shares) }} share
                </p>
              </div>
              <div class="flex flex-col gap-2 lg:w-56 lg:shrink-0">
                <UCheckbox
                  v-for="m in REVIEW_MARKS"
                  :key="m.key"
                  :model-value="p.marks.includes(m.key)"
                  :label="m.label"
                  :disabled="saving === `${p.platform}:${p.content_id}`"
                  @update:model-value="v => setMark(p, m.key, v)"
                />
              </div>
            </UCard>
          </li>
        </ul>
        <ListPager
          v-model:page="page"
          :total="total"
          :page-size="pageSize"
        />
      </div>
    </template>
  </UDashboardPanel>
</template>
