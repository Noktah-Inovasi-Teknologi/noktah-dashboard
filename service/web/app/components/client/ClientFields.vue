<script setup lang="ts">
import type { ClientForm } from '~/composables/useClientForm'

/** Name, status, Jira component, quotas and folders of a Client. */
defineProps<{ disabled?: boolean }>()
const form = defineModel<ClientForm>({ required: true })
const QUOTAS = [{ key: 'quota_post', label: 'Post' }, { key: 'quota_story', label: 'Story' }, { key: 'quota_short_video', label: 'Short Video' }] as const
</script>

<template>
  <div class="space-y-4">
    <div class="grid gap-3 sm:grid-cols-2">
      <UFormField
        label="Nama"
        required
        class="sm:col-span-2"
      >
        <UInput
          v-model="form.name"
          :disabled="disabled"
          class="w-full"
        />
      </UFormField>
      <UFormField label="Status">
        <USelect
          v-model="form.status"
          :items="CLIENT_STATUS_ITEMS"
          :disabled="disabled"
          class="w-full"
        />
      </UFormField>
      <UFormField
        label="Komponen Jira"
        help="ID komponen (angka), mis. 10042."
      >
        <UInput
          v-model="form.jira_component_id"
          :disabled="disabled"
          class="w-full"
        />
      </UFormField>
    </div>
    <fieldset>
      <legend class="text-sm font-medium mb-2">
        Kuota per bulan
      </legend>
      <div class="grid grid-cols-3 gap-3">
        <UFormField
          v-for="q in QUOTAS"
          :key="q.key"
          :label="q.label"
        >
          <UInputNumber
            v-model="form[q.key]"
            :min="0"
            :disabled="disabled"
            class="w-full"
          />
        </UFormField>
      </div>
    </fieldset>
    <div class="grid gap-3">
      <UFormField
        label="Folder Drive"
        help="ID folder, bukan tautan."
      >
        <UTextarea
          v-model="form.drive_folder_id"
          :rows="1"
          autoresize
          :disabled="disabled"
          class="w-full"
          :ui="{ base: 'font-mono text-xs break-all resize-none' }"
        />
      </UFormField>
      <UFormField
        label="Folder content plan"
        help="ID folder, bukan tautan."
      >
        <UTextarea
          v-model="form.content_plan_folder_id"
          :rows="1"
          autoresize
          :disabled="disabled"
          class="w-full"
          :ui="{ base: 'font-mono text-xs break-all resize-none' }"
        />
      </UFormField>
    </div>
  </div>
</template>
