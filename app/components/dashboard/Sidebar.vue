<script setup lang="ts">
const kindeClient = useKindeClient();

interface Props {
  modelValue?: boolean;
}

interface Emits {
  (e: "update:modelValue", value: boolean): void;
}

const props = withDefaults(defineProps<Props>(), {
  modelValue: false,
});

const emit = defineEmits<Emits>();

const isCollapsed = computed({
  get: () => props.modelValue,
  set: (value) => emit("update:modelValue", value),
});

const navigationItems = [
  {
    path: "/",
    label: "Dashboard",
    icon: "i-heroicons-home",
  },
  {
    path: "/libraries",
    label: "Libraries",
    icon: "i-heroicons-document-text",
  },
  {
    path: "/agents",
    label: "AI Agents",
    icon: "i-heroicons-cpu-chip",
  },
  {
    path: "/workflows",
    label: "Workflows",
    icon: "i-heroicons-arrows-right-left",
    permissions: ["noktah-dashboard:admin"],
  },
  {
    path: "/settings",
    label: "Settings",
    icon: "i-heroicons-cog-6-tooth",
    permissions: ["noktah-dashboard:admin"],
  },
];

const toggleSidebar = () => {
  isCollapsed.value = !isCollapsed.value;
};

const { data: permissions } = await useAsyncData(async () => {
  const { permissions } = (await kindeClient?.getPermissions()) ?? {};
  return permissions;
});

const filteredNavigationItems = computed(() => {
  return navigationItems.filter(item => {
    if (!item.permissions) return true;
    if (!permissions.value) return false;
    
    return item.permissions.every(requiredPermission => 
      permissions.value?.includes(requiredPermission) ?? false
    );
  });
});
</script>

<template>
  <aside
    :class="[
      'bg-gray-900 text-white border-r border-gray-200 dark:border-gray-700 transition-all flex flex-col duration-normal ease-smooth',
      isCollapsed ? 'w-auto' : 'w-container-sidebar',
    ]"
  >
    <div class="border-b border-gray-700 p-comfortable">
      <div class="flex items-center justify-between">
        <h1 v-if="!isCollapsed" class="font-bold text-white text-header-5">
          Noktah Dashboard
        </h1>
        <button
          @click="toggleSidebar"
          class="hover:bg-gray-700 transition-colors p-small rounded-custom"
        >
          <UIcon
            :name="
              isCollapsed
                ? 'i-heroicons-chevron-right'
                : 'i-heroicons-chevron-left'
            "
            class="w-5 h-5"
          />
        </button>
      </div>
    </div>
    <nav class="flex-1 p-comfortable">
      <ul class="flex flex-col gap-small">
        <li v-for="item in filteredNavigationItems" :key="item.path">
          <NuxtLink
            :to="item.path"
            :class="[
              'flex items-center hover:bg-gray-700 transition-colors p-small rounded-custom',
              isCollapsed ? 'justify-center' : 'gap-small',
            ]"
            active-class="bg-gray-700"
          >
            <UIcon :name="item.icon" class="w-5 h-5 flex-shrink-0" />
            <span v-if="!isCollapsed" class="font-medium text-paragraph-2">{{
              item.label
            }}</span>
          </NuxtLink>
        </li>
      </ul>
    </nav>
  </aside>
</template>
