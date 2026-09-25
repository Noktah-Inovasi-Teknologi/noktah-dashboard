<script setup lang="ts">
import type { CardDefinition, Shape, SubfieldSpec } from '~/types/hub'

/** Read-only rendering of one Card value, by its shape in the card definition. */
const props = defineProps<{
  shape: Shape
  value: unknown
  subfields?: SubfieldSpec[]
  choices?: string
  definition?: CardDefinition | null
}>()

const asList = (v: unknown) => (Array.isArray(v) ? v.filter(x => !isEmptyValue(x)).map(String) : [])
const asObject = (v: unknown) => (v && typeof v === 'object' && !Array.isArray(v) ? v as Record<string, unknown> : {})
const lines = computed(() => (Array.isArray(props.value) ? props.value as Record<string, unknown>[] : []))
const filledSubs = computed(() => (props.subfields ?? []).filter(s => !isEmptyValue(asObject(props.value)[s.key])))
</script>

<template>
  <p
    v-if="isEmptyValue(value)"
    class="text-sm text-muted italic"
  >
    Belum diisi
  </p>

  <p
    v-else-if="shape === 'text'"
    class="text-sm whitespace-pre-line break-words"
  >
    {{ value }}
  </p>

  <!-- Short items (words, hashtags) read best as chips; sentences get cut off
       inside a badge, so a list with any long item is a bullet list. -->
  <ul
    v-else-if="shape === 'list' && asList(value).some(x => x.length > 32)"
    class="list-disc ps-5 text-sm space-y-1"
  >
    <li
      v-for="(item, i) in asList(value)"
      :key="i"
      class="break-words"
    >
      {{ item }}
    </li>
  </ul>
  <div
    v-else-if="shape === 'list'"
    class="flex flex-wrap gap-1"
  >
    <UBadge
      v-for="(item, i) in asList(value)"
      :key="i"
      :label="item"
      color="neutral"
      variant="subtle"
      class="max-w-full whitespace-normal break-words"
    />
  </div>

  <span
    v-else-if="shape === 'rating'"
    class="text-sm"
  >{{ value }}/5</span>

  <span
    v-else-if="shape === 'choice'"
    class="text-sm"
  >{{ choiceLabel(definition, choices, value) }}</span>

  <dl
    v-else-if="shape === 'object'"
    class="grid items-start gap-x-4 gap-y-2 sm:grid-cols-[minmax(6rem,11rem)_minmax(0,1fr)] text-sm"
  >
    <template
      v-for="sub in filledSubs"
      :key="sub.key"
    >
      <dt class="text-muted">
        {{ sub.label }}
      </dt>
      <dd class="min-w-0">
        <CardValueView
          :shape="sub.shape"
          :value="asObject(value)[sub.key]"
          :choices="sub.choices"
          :definition="definition"
        />
      </dd>
    </template>
  </dl>

  <ul
    v-else-if="shape === 'lines'"
    class="space-y-2"
  >
    <li
      v-for="(line, i) in lines"
      :key="i"
      class="rounded-md border border-default p-2 text-sm"
    >
      <dl class="grid items-start gap-x-3 gap-y-1 sm:grid-cols-[minmax(5rem,8rem)_minmax(0,1fr)]">
        <template
          v-for="sub in (subfields ?? []).filter(s => !isEmptyValue(line[s.key]))"
          :key="sub.key"
        >
          <dt class="text-muted">
            {{ sub.label }}
          </dt>
          <dd class="min-w-0 break-words">
            {{ line[sub.key] }}
          </dd>
        </template>
      </dl>
    </li>
  </ul>
</template>
