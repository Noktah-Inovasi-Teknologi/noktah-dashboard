<script setup lang="ts">
const sidebarCollapsed = ref(false);
const pageTitle = ref("Dashboard");
const pageDescription = ref("Manage your data and AI workflows");
const showCreateButton = ref(true);

const route = useRoute();

watch(
  () => route.path,
  (newPath) => {
    switch (newPath) {
      case "/":
        pageTitle.value = "Dashboard";
        pageDescription.value = "Overview of your data and workflows";
        break;
      case "/analytics":
        pageTitle.value = "Analytics";
        pageDescription.value = "View insights and performance metrics";
        break;
      case "/content":
        pageTitle.value = "Content Management";
        pageDescription.value = "Create and manage your content";
        break;
      case "/ai-agents":
        pageTitle.value = "AI Agents";
        pageDescription.value = "Configure and monitor AI agents";
        break;
      case "/workflows":
        pageTitle.value = "Workflows";
        pageDescription.value = "Manage your automated workflows";
        break;
      case "/settings":
        pageTitle.value = "Settings";
        pageDescription.value = "Configure your dashboard preferences";
        showCreateButton.value = false;
        break;
      default:
        pageTitle.value = "Dashboard";
        pageDescription.value = "";
    }
  },
  { immediate: true }
);

const handleCreate = () => {
  console.log("Create new item");
};

const handleRefresh = () => {
  console.log("Refresh data");
  refreshCookie("nuxt-session");
};
</script>

<template>
  <div class="flex min-h-screen bg-gray-50 dark:bg-gray-900">
    <DashboardSidebar v-model="sidebarCollapsed" />

    <div class="flex flex-col flex-1">
      <DashboardNavbar
        :page-title="pageTitle"
        :page-description="pageDescription"
        :show-create-button="showCreateButton"
        @create="handleCreate"
        @refresh="handleRefresh"
      />

      <main class="flex-1 p-comfortable">
        <NuxtPage />
      </main>
    </div>
  </div>
</template>
