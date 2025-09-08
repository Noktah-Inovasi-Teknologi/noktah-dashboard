<template>
  <div class="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 dark:from-gray-900 dark:to-gray-800">
    <!-- Header Section -->
    <!-- <header class="text-center p-spacious">
      <h1 class="font-bold text-gray-900 dark:text-white text-header-1 mb-comfortable">
        Noktah Dashboard
      </h1>
      <p class="text-gray-600 dark:text-gray-300 max-w-2xl mx-auto text-paragraph-2 mb-spacious">
        Enterprise dashboard platform for AI-powered data analysis and content creation
      </p>
    </header> -->

    <!-- Main Content -->
    <main class="container mx-auto px-4">
      <div class="flex flex-col items-center justify-center gap-6">
        <!-- Dashboard Preview Card -->
        <div class="bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 w-full max-w-lg p-spacious rounded-custom shadow-moderate backdrop-blur-light">

          <div class="text-center mb-comfortable">
            <h2 class="font-semibold text-gray-900 dark:text-white text-header-4 mb-small">
              Get Started
            </h2>
            <p class="text-gray-600 dark:text-gray-400 text-paragraph-3">
              Access your AI-powered analytics and insights
            </p>
          </div>

          <!-- Action Buttons -->
          <div class="space-y-small">
            <NuxtLink 
              to="/api/login" 
              external
              class="w-full bg-blue-600 hover:bg-blue-700 text-white font-medium py-comfortable px-spacious transition-all block text-center rounded-custom text-paragraph-2 duration-fast ease-smooth">
              Sign In
            </NuxtLink>
            
            <NuxtLink 
              to="/api/register" 
              external
              class="w-full bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-900 dark:text-white font-medium py-comfortable px-spacious transition-all block text-center rounded-custom text-paragraph-2 duration-fast ease-smooth">
              Sign Up
            </NuxtLink>
          </div>
        </div>

        <!-- Features Grid -->
        <div class="grid grid-cols-1 md:grid-cols-3 gap-spacious w-full max-w-4xl mt-spacious">
          <div class="bg-white/80 dark:bg-gray-800/80 text-center p-spacious rounded-custom shadow-subtle backdrop-blur-light">
            <h3 class="font-semibold text-gray-900 dark:text-white text-header-6 mb-small">
              AI Analytics
            </h3>
            <p class="text-gray-600 dark:text-gray-400 text-paragraph-4">
              Advanced data insights powered by machine learning
            </p>
          </div>

          <div class="bg-white/80 dark:bg-gray-800/80 text-center p-spacious rounded-custom shadow-subtle backdrop-blur-light">
            <h3 class="font-semibold text-gray-900 dark:text-white text-header-6 mb-small">
              Real-time Data
            </h3>
            <p class="text-gray-600 dark:text-gray-400 text-paragraph-4">
              Live monitoring and instant notifications
            </p>
          </div>

          <div class="bg-white/80 dark:bg-gray-800/80 text-center p-spacious rounded-custom shadow-subtle backdrop-blur-light">
            <h3 class="font-semibold text-gray-900 dark:text-white text-header-6 mb-small">
              Workflow Automation
            </h3>
            <p class="text-gray-600 dark:text-gray-400 text-paragraph-4">
              Streamline processes with intelligent automation
            </p>
          </div>
        </div>
      </div>
    </main>
  </div>
</template>
