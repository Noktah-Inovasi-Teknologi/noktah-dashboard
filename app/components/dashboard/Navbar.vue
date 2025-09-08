<script setup lang="ts">
interface Props {
  pageTitle?: string;
  pageDescription?: string;
  showCreateButton?: boolean;
}

interface Emits {
  (e: "create"): void;
  (e: "refresh"): void;
}

const props = withDefaults(defineProps<Props>(), {
  pageTitle: "Dashboard",
  pageDescription: "",
  showCreateButton: true,
});

const emit = defineEmits<Emits>();

const searchQuery = ref("");

const userMenuItems = [
  [
    {
      label: "Sign out",
      icon: "i-heroicons-arrow-right-on-rectangle",
      color: "red",
      to: "/api/logout",
      external: true,
    },
  ],
];

const actionMenuItems = [
  [
    {
      label: "Export Data",
      icon: "i-heroicons-arrow-down-tray",
      click: () => console.log("Export clicked"),
    },
    {
      label: "Import Data",
      icon: "i-heroicons-arrow-up-tray",
      click: () => console.log("Import clicked"),
    },
  ],
  [
    {
      label: "Print",
      icon: "i-heroicons-printer",
      click: () => window.print(),
    },
    {
      label: "Share",
      icon: "i-heroicons-share",
      click: () => console.log("Share clicked"),
    },
  ],
];
</script>

<template>
  <header class="bg-white dark:bg-gray-900 shadow-sm border-b border-gray-200 dark:border-gray-700 backdrop-blur-sm p-comfortable">
    <div class="flex items-center justify-between gap-comfortable">
      <!-- Left Section: Search -->
      <div class="flex items-center gap-comfortable">
        <div class="relative flex items-center gap-small bg-gray-50 dark:bg-gray-800 hover:bg-gray-100 dark:hover:bg-gray-700 focus-within:bg-white dark:focus-within:bg-gray-800 focus-within:ring-2 focus-within:ring-blue-500/20 rounded-custom px-compact py-small border border-transparent focus-within:border-blue-500/50 transition-all duration-fast">
          <UIcon
            name="i-heroicons-magnifying-glass"
            class="w-5 h-5 text-gray-400 dark:text-gray-500 focus-within:text-blue-500 transition-colors duration-fast"
          />
          <UInput
            v-model="searchQuery"
            placeholder="Search dashboard..."
            class="w-container-sidebar bg-transparent border-none focus:ring-0 text-paragraph-3"
            size="sm"
            variant="none"
          />
        </div>
      </div>

      <!-- Right Section: Actions & User -->
      <div class="flex items-center gap-small">
        <!-- Notifications -->
        <UButton 
          variant="ghost" 
          size="sm" 
          class="relative hover:bg-gray-100 dark:hover:bg-gray-800 hover:-translate-y-0.5 transition-all duration-fast p-small rounded-custom"
        >
          <UIcon name="i-heroicons-bell" class="w-5 h-5 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300" />
          <span class="absolute -top-1 -right-1 bg-red-500 text-white rounded-full w-4 h-4 flex items-center justify-center text-paragraph-7 font-weight-semibold animate-pulse shadow-lg">
            3
          </span>
        </UButton>

        <!-- Help -->
        <UButton 
          variant="ghost" 
          size="sm" 
          class="hover:bg-gray-100 dark:hover:bg-gray-800 hover:-translate-y-0.5 transition-all duration-fast p-small rounded-custom"
        >
          <UIcon name="i-heroicons-question-mark-circle" class="w-5 h-5 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-300" />
        </UButton>

        <!-- User Menu -->
        <UDropdownMenu :items="userMenuItems">
          <UButton
            variant="ghost"
            size="sm"
            class="flex items-center gap-small hover:bg-gray-100 dark:hover:bg-gray-800 hover:-translate-y-0.5 transition-all duration-fast p-small rounded-custom"
          >
            <div class="relative">
              <img
                v-if="$auth.user?.picture"
                :src="$auth.user.picture"
                :alt="`${$auth.user?.given_name} ${$auth.user?.family_name}`"
                class="w-8 h-8 rounded-full object-cover border-2 border-white dark:border-gray-700 shadow-sm"
                crossorigin="anonymous"
              />
              <div
                v-else
                class="w-8 h-8 bg-gradient-to-br from-blue-500 to-blue-600 rounded-full flex items-center justify-center shadow-sm"
              >
                <UIcon name="i-heroicons-user" class="w-4 h-4 text-white" />
              </div>
            </div>
            <div class="hidden sm:block text-left">
              <p class="font-weight-medium text-gray-700 dark:text-gray-300 text-paragraph-3 leading-tight">
                {{ `${$auth.user?.given_name} ${$auth.user?.family_name}` || 'User' }}
              </p>
              <p class="text-gray-500 dark:text-gray-400 text-paragraph-5 leading-tight">
                {{ $auth.user?.email || 'user@example.com' }}
              </p>
            </div>
            <UIcon
              name="i-heroicons-chevron-down"
              class="w-4 h-4 text-gray-400 transition-transform duration-fast group-hover:translate-y-0.5"
            />
          </UButton>
        </UDropdownMenu>
      </div>
    </div>

  </header>
</template>
