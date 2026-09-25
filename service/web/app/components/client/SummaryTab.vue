<script setup lang="ts">
import type { Card, CardDefinition } from '~/types/hub'

/** Ringkasan: the automatic overview (G-27) plus what is still missing on the card. */
const props = defineProps<{ card: Card, definition: CardDefinition }>()
const label = (part: 'profil' | 'guideline', key: string) =>
  props.definition.parts[part].fields.find(f => f.key === key)?.label ?? key
</script>

<template>
  <div class="grid gap-4 lg:grid-cols-3">
    <UCard
      class="lg:col-span-2"
      :ui="{ body: 'space-y-4' }"
    >
      <template #header>
        <div class="flex flex-wrap items-center gap-2">
          <h3 class="font-medium text-highlighted">
            Ringkasan
          </h3>
          <UBadge
            label="Dibuat otomatis"
            color="neutral"
            variant="subtle"
          />
          <span
            v-if="card.summary"
            class="text-xs text-muted"
          >{{ formatDate(card.summary.generated_at) }}</span>
        </div>
      </template>
      <p
        v-if="!card.summary"
        class="text-sm text-muted"
      >
        Belum ada ringkasan. Ringkasan dibuat otomatis setelah kartu mulai terisi.
      </p>
      <template v-else>
        <UAlert
          v-if="card.summary.stale"
          color="warning"
          variant="subtle"
          icon="i-lucide-clock"
          :title="card.summary.cap_paused ? 'Ringkasan tertunda: batas biaya AI bulan ini tercapai' : 'Kartu sudah berubah; ringkasan diperbarui paling lambat besok'"
        />
        <p class="text-sm">
          {{ card.summary.body.siapa }}
        </p>
        <div v-if="card.summary.body.promo_berjalan.length">
          <h4 class="text-sm font-medium mb-1">
            Promo berjalan
          </h4>
          <ul class="list-disc ps-5 text-sm space-y-1">
            <li
              v-for="(p, i) in card.summary.body.promo_berjalan"
              :key="i"
            >
              {{ p }}
            </li>
          </ul>
        </div>
        <div v-if="card.summary.body.aturan_kunci.length">
          <h4 class="text-sm font-medium mb-1">
            Aturan kunci
          </h4>
          <ul class="list-disc ps-5 text-sm space-y-1">
            <li
              v-for="(p, i) in card.summary.body.aturan_kunci"
              :key="i"
            >
              {{ p }}
            </li>
          </ul>
        </div>
        <div v-if="card.summary.body.permintaan_terbuka.length">
          <h4 class="text-sm font-medium mb-1">
            Permintaan terbuka
          </h4>
          <ul class="list-disc ps-5 text-sm space-y-1">
            <li
              v-for="(p, i) in card.summary.body.permintaan_terbuka"
              :key="i"
            >
              {{ p }}
            </li>
          </ul>
        </div>
        <p class="text-xs text-muted">
          Ringkasan hanya memakai isian kartu yang sudah dikonfirmasi, dan tidak pernah dipakai sebagai sumber.
        </p>
      </template>
    </UCard>

    <UCard :ui="{ body: 'space-y-4' }">
      <template #header>
        <h3 class="font-medium text-highlighted">
          Kelengkapan
        </h3>
      </template>
      <div>
        <div class="flex justify-between text-sm mb-1">
          <span>Profil (wajib)</span>
          <span class="tabular-nums">{{ card.completeness.profil.filled }}/{{ card.completeness.profil.required }}</span>
        </div>
        <UProgress
          :model-value="card.completeness.profil.filled"
          :max="card.completeness.profil.required"
          size="sm"
        />
        <ul
          v-if="card.completeness.profil.missing.length"
          class="mt-2 text-xs text-muted list-disc ps-5"
        >
          <li
            v-for="k in card.completeness.profil.missing"
            :key="k"
          >
            {{ label('profil', k) }}
          </li>
        </ul>
      </div>
      <div>
        <div class="flex justify-between text-sm mb-1">
          <span>Guideline</span>
          <span class="tabular-nums">{{ card.completeness.guideline.filled }}/{{ card.completeness.guideline.total }}</span>
        </div>
        <UProgress
          :model-value="card.completeness.guideline.filled"
          :max="card.completeness.guideline.total"
          size="sm"
        />
        <ul
          v-if="card.completeness.guideline.missing_required.length"
          class="mt-2 text-xs text-muted list-disc ps-5"
        >
          <li
            v-for="k in card.completeness.guideline.missing_required"
            :key="k"
          >
            {{ label('guideline', k) }} (wajib)
          </li>
        </ul>
      </div>
      <p class="text-sm">
        {{ card.requests_open }} permintaan terbuka
      </p>
    </UCard>
  </div>
</template>
