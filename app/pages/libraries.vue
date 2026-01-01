<script lang="ts" setup>
interface Memory {
  messages: {
    role: string
    content: string
  }[]
  agent_id: string
  user_id: string
  timestamp: string
}

const selectedTab = ref('add-memories')

const state = reactive({
  messages: [{ role: '', content: '' }],
  agent_id: '',
  user_id: '',
  timestamp: new Date().toISOString().slice(0, 16) // Format for datetime-local input
})

const memories = ref<Memory[]>([])
const isLoading = ref(false)
const error = ref<string | null>(null)

const tabs = [
  {
    key: 'add-memories',
    label: 'Add Memories',
    slot: 'add-memories' as const,
  },
  {
    key: 'memories',
    label: 'Memories',
    slot: 'memories' as const
  }
]

const roleOptions = [
  { label: 'Assistant', value: 'assistant' },
  { label: 'User', value: 'user' }
]

function addMessage() {
  state.messages.push({ role: '', content: '' })
}

function removeMessage(index: number) {
  if (state.messages.length > 1) {
    state.messages.splice(index, 1)
  }
}
</script>

<template>
  <div class="flex flex-col min-h-screen bg-gray-50 dark:bg-gray-900">
    <!-- Header Section -->
    <div class="flex flex-col gap-4 p-6 bg-white dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
      <div class="flex flex-col gap-2">
        <h1 class="text-3xl font-bold text-gray-900 dark:text-white">Libraries</h1>
        <p class="text-gray-600 dark:text-gray-400">Manage your memory libraries and add new memories</p>
      </div>
      
      <UTabs v-model="selectedTab" :items="tabs" class="w-full">
        <!-- Add Memories Tab Content -->
        <template #add-memories>
          <div class="flex justify-center pt-6">
            <div class="w-full max-w-4xl">
              <div class="bg-white dark:bg-gray-800 rounded-xl shadow-sm border border-gray-200 dark:border-gray-700">
                <div class="flex flex-col gap-6 p-8">
                  <div class="flex flex-col gap-2">
                    <h2 class="text-xl font-semibold text-gray-900 dark:text-white">Add New Memory</h2>
                    <p class="text-sm text-gray-600 dark:text-gray-400">Create a new memory entry with conversation messages</p>
                  </div>
                  
                  <UForm :state="state" class="flex flex-col gap-8">
                    <!-- Messages Section -->
                    <div class="flex flex-col gap-4">
                      <div class="flex flex-col gap-2">
                        <label class="text-sm font-medium text-gray-700 dark:text-gray-300">Messages</label>
                        <p class="text-xs text-gray-500 dark:text-gray-400">Add conversation messages between user and assistant</p>
                      </div>
                      
                      <div class="flex flex-col gap-4">
                        <div 
                          v-for="(message, index) in state.messages" 
                          :key="index" 
                          class="flex flex-col gap-4 bg-gray-50 dark:bg-gray-700/30 border border-gray-200 dark:border-gray-600 rounded-lg p-6"
                        >
                          <div class="flex flex-row items-center justify-between">
                            <span class="text-sm font-medium text-gray-800 dark:text-gray-200">
                              Message
                            </span>
                            <UButton
                              v-if="state.messages.length > 1"
                              @click="removeMessage(index)"
                              color="error"
                              variant="ghost"
                              size="sm"
                              icon="i-heroicons-trash"
                            >
                              Remove
                            </UButton>
                          </div>
                          
                          <div class="flex flex-col lg:flex-row gap-4">
                            <div class="flex flex-col gap-2 lg:w-48">
                              <label class="text-xs font-medium text-gray-600 dark:text-gray-400">Role</label>
                              <USelect
                                v-model="message.role"
                                :options="roleOptions"
                                placeholder="Select role"
                              />
                            </div>
                            <div class="flex flex-col gap-2 flex-1">
                              <label class="text-xs font-medium text-gray-600 dark:text-gray-400">Content</label>
                              <UTextarea
                                v-model="message.content"
                                placeholder="Enter message content..."
                                :rows="4"
                                class="w-full"
                              />
                            </div>
                          </div>
                        </div>
                      </div>
                      
                      <div class="flex justify-start">
                        <UButton
                          @click="addMessage"
                          variant="outline"
                          icon="i-heroicons-plus"
                          size="sm"
                        >
                          Add Message
                        </UButton>
                      </div>
                    </div>

                    <!-- Metadata Section -->
                    <div class="flex flex-col gap-4">
                      <div class="flex flex-col gap-2">
                        <label class="text-sm font-medium text-gray-700 dark:text-gray-300">Memory Metadata</label>
                        <p class="text-xs text-gray-500 dark:text-gray-400">Identifiers and timestamp for this memory</p>
                      </div>
                      
                      <div class="flex flex-col lg:flex-row gap-4">
                        <div class="flex flex-col gap-2 flex-1">
                          <label class="text-xs font-medium text-gray-600 dark:text-gray-400">Agent ID</label>
                          <UInput
                            v-model="state.agent_id"
                            placeholder="e.g., agent-001"
                          />
                        </div>
                        <div class="flex flex-col gap-2 flex-1">
                          <label class="text-xs font-medium text-gray-600 dark:text-gray-400">User ID</label>
                          <UInput
                            v-model="state.user_id"
                            placeholder="e.g., user-123"
                          />
                        </div>
                      </div>
                      
                      <div class="flex flex-col gap-2">
                        <label class="text-xs font-medium text-gray-600 dark:text-gray-400">Timestamp</label>
                        <UInput
                          v-model="state.timestamp"
                          type="datetime-local"
                          class="w-full lg:w-80"
                        />
                      </div>
                    </div>

                    <!-- Action Buttons -->
                    <div class="flex flex-col sm:flex-row gap-3 pt-6 border-t border-gray-200 dark:border-gray-700">
                      <UButton
                        @click="saveMemory"
                        color="primary"
                        size="lg"
                        icon="i-heroicons-check"
                        class="flex-1 sm:flex-none"
                      >
                        Save Memory
                      </UButton>
                      <UButton
                        @click="selectedTab = 'memories'"
                        variant="outline"
                        size="lg"
                        class="flex-1 sm:flex-none"
                      >
                        View Memories
                      </UButton>
                    </div>
                  </UForm>
                </div>
              </div>
            </div>
          </div>
        </template>
      </UTabs>
    </div>
  </div>
</template>
