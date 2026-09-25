<script setup lang="ts">
import type { PersonForm } from '~/composables/usePersonForm'

/** Name, IDs, emails and roles: the fields of "Tambah orang" and of a Person's profile. */
defineProps<{ roleItems: { label: string, value: string, disabled?: boolean }[], disabled?: boolean }>()
const form = defineModel<PersonForm>({ required: true })
</script>

<template>
  <div class="space-y-3">
    <UFormField
      label="Nama"
      required
    >
      <UInput
        v-model="form.display_name"
        :disabled="disabled"
        class="w-full"
      />
    </UFormField>
    <UFormField
      label="Email"
      help="Tekan Enter setelah tiap email. Semua email ini masuk sebagai orang yang sama."
    >
      <UInputTags
        v-model="form.emails"
        :disabled="disabled"
        placeholder="nama@contoh.com"
        class="w-full"
      />
    </UFormField>
    <UFormField
      label="Peran"
      help="Boleh lebih dari satu. Tanpa peran Manager, orang ini tidak bisa masuk ke Hub."
    >
      <USelectMenu
        v-model="form.roles"
        :items="roleItems"
        value-key="value"
        multiple
        :search-input="{ placeholder: 'Cari peran…' }"
        placeholder="Pilih peran…"
        :disabled="disabled"
        class="w-full"
      />
    </UFormField>
    <div class="grid gap-3 sm:grid-cols-2">
      <UFormField label="Jira account ID">
        <UInput
          v-model="form.jira_account_id"
          :disabled="disabled"
          class="w-full"
          :ui="{ base: 'font-mono text-xs' }"
        />
      </UFormField>
      <UFormField label="Slack user ID">
        <UInput
          v-model="form.slack_user_id"
          :disabled="disabled"
          class="w-full"
          :ui="{ base: 'font-mono text-xs' }"
        />
      </UFormField>
    </div>
  </div>
</template>
