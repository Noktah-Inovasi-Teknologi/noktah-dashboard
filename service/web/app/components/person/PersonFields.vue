<script setup lang="ts">
import type { Choice, PersonForm } from '~/composables/usePersonForm'

/**
 * Name, emails, Units, roles, permissions and IDs: the fields of "Tambah orang" and
 * of a Person's page. Picking a role adds the permissions its catalog entry suggests
 * (only those the manager may give); taking a Unit away takes its roles with it.
 */
const props = defineProps<{
  choices: {
    units: Choice[]
    roles: Choice[]
    permissions: { key: string, label: string, help: string, disabled: boolean }[]
    mayPermission: (p: string) => boolean
  }
  /** The Person's current client teams, to warn when a removed role releases them. */
  team?: { client_id: string, team_role: string, brand: string | null }[]
  disabled?: boolean
}>()
const form = defineModel<PersonForm>({ required: true })
const { roleIn, roleName } = useCatalog()

function onUnits(units: string[]) {
  form.value.units = units
  form.value.roles = form.value.roles.filter(v => units.includes(parseRole(v).brand ?? ''))
}

function onRoles(roles: string[]) {
  const added = roles.filter(v => !form.value.roles.includes(v))
  form.value.roles = roles
  const perms = new Set(form.value.permissions)
  for (const v of added) {
    const { role, brand } = parseRole(v)
    for (const p of roleIn(role, brand)?.default_permissions ?? []) {
      if (props.choices.mayPermission(p)) perms.add(p)
    }
  }
  form.value.permissions = PERMISSIONS.map(p => p.key).filter(k => perms.has(k))
}

function onPermissions(perms: string[]) {
  form.value.permissions = withHubAccess(perms)
}

// Removing a role that fills client team slots releases those slots on save.
const released = computed(() => {
  const kept = new Set(form.value.roles)
  return (props.team ?? []).filter(t => !kept.has(roleValue(t.team_role, t.brand)))
})
const releasedNote = computed(() => {
  if (!released.value.length) return ''
  const roles = [...new Set(released.value.map(t => roleName(t.team_role)))].join(', ')
  const clients = new Set(released.value.map(t => t.client_id)).size
  return `Saat disimpan, orang ini keluar dari tim ${clients} klien sebagai ${roles}.`
})

const lockedRoles = computed(() => props.choices.roles.some(i => i.disabled))
const lockedPermissions = computed(() => props.choices.permissions.some(p => p.disabled))
const permissionItems = computed(() => props.choices.permissions.map(p => ({
  label: p.label, description: p.help, value: p.key, disabled: p.disabled || props.disabled
})))
</script>

<template>
  <div class="space-y-4">
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
      help="Tekan Enter setelah tiap email; semua email ini masuk sebagai orang yang sama."
    >
      <UInputTags
        v-model="form.emails"
        :disabled="disabled"
        placeholder="nama@contoh.com"
        class="w-full"
      />
    </UFormField>
    <UFormField
      label="Unit"
      help="Noktah untuk peran di semua Noktah Brand, seperti Owner dan Sales & Marketing."
    >
      <USelectMenu
        :model-value="form.units"
        :items="choices.units"
        value-key="value"
        multiple
        :search-input="false"
        placeholder="Pilih unit…"
        :disabled="disabled"
        class="w-full"
        @update:model-value="onUnits"
      />
    </UFormField>
    <UFormField
      label="Peran"
      :help="`Boleh lebih dari satu. Tim klien hanya bisa diisi orang yang memegang perannya.${lockedRoles ? ' Peran yang terkunci di luar wewenang Anda.' : ''}`"
    >
      <USelectMenu
        :model-value="form.roles"
        :items="choices.roles"
        value-key="value"
        multiple
        :search-input="{ placeholder: 'Cari peran…' }"
        :placeholder="form.units.length ? 'Pilih peran…' : 'Pilih unit dulu'"
        :disabled="disabled || !form.units.length"
        class="w-full"
        @update:model-value="onRoles"
      />
    </UFormField>
    <UAlert
      v-if="releasedNote && !disabled"
      color="warning"
      variant="subtle"
      icon="i-lucide-users"
      :title="releasedNote"
    />
    <UFormField
      label="Izin"
      :help="`Menentukan apa yang bisa dilakukan di Hub. Memilih peran ikut mencentang izin bawaannya; ubah bila perlu.${lockedPermissions ? ' Anda hanya bisa mengatur izin yang Anda punya sendiri.' : ''}`"
    >
      <UCheckboxGroup
        :model-value="form.permissions"
        :items="permissionItems"
        :disabled="disabled"
        :ui="{ fieldset: 'grid gap-3 sm:grid-cols-2' }"
        @update:model-value="v => onPermissions(v as string[])"
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
