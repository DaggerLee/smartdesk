<template>
  <div class="auth-screen">
    <div class="auth-card">
      <div class="auth-logo">🤖</div>
      <h1 class="auth-title">SmartDesk</h1>
      <p class="auth-subtitle">Enterprise Knowledge Assistant</p>

      <!-- Tab switcher -->
      <div class="auth-tabs">
        <button
          class="auth-tab"
          :class="{ active: mode === 'login' }"
          @click="switchMode('login')"
        >Sign In</button>
        <button
          class="auth-tab"
          :class="{ active: mode === 'register' }"
          @click="switchMode('register')"
        >Create Account</button>
      </div>

      <!-- Form -->
      <form class="auth-form" @submit.prevent="handleSubmit">
        <div class="field">
          <label>Username</label>
          <input
            v-model="form.username"
            type="text"
            placeholder="Enter your username"
            autocomplete="username"
            ref="usernameInput"
          />
        </div>
        <div class="field">
          <label>Password</label>
          <input
            v-model="form.password"
            type="password"
            :placeholder="mode === 'register' ? 'At least 6 characters' : 'Enter your password'"
            autocomplete="current-password"
          />
        </div>

        <div v-if="error" class="auth-error">{{ error }}</div>

        <button type="submit" class="btn-submit" :disabled="loading">
          <span v-if="loading">{{ mode === 'login' ? 'Signing in…' : 'Creating account…' }}</span>
          <span v-else>{{ mode === 'login' ? 'Sign In' : 'Create Account' }}</span>
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { nextTick, ref } from "vue";
import { login, register } from "../api/index.js";

const emit = defineEmits(["authenticated"]);

const mode = ref("login");
const form = ref({ username: "", password: "" });
const error = ref("");
const loading = ref(false);
const usernameInput = ref(null);

function switchMode(m) {
  mode.value = m;
  form.value = { username: "", password: "" };
  error.value = "";
  nextTick(() => usernameInput.value?.focus());
}

async function handleSubmit() {
  error.value = "";
  const { username, password } = form.value;
  if (!username.trim() || !password) {
    error.value = "Please fill in all fields.";
    return;
  }
  loading.value = true;
  try {
    const data = mode.value === "login"
      ? await login(username.trim(), password)
      : await register(username.trim(), password);
    emit("authenticated", data.username);
  } catch (err) {
    error.value = err.response?.data?.detail || "Something went wrong. Please try again.";
  } finally {
    loading.value = false;
  }
}
</script>

<style scoped>
.auth-screen {
  width: 100%;
  height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-bg);
}

.auth-card {
  width: 380px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 16px;
  padding: 40px 36px 36px;
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.08);
  display: flex;
  flex-direction: column;
  align-items: center;
}

.auth-logo {
  font-size: 44px;
  margin-bottom: 10px;
}

.auth-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text);
  margin: 0 0 4px;
}

.auth-subtitle {
  font-size: 13px;
  color: var(--color-text-muted);
  margin: 0 0 28px;
}

/* Tabs */
.auth-tabs {
  display: flex;
  width: 100%;
  background: var(--color-bg);
  border-radius: 8px;
  padding: 3px;
  margin-bottom: 24px;
  gap: 2px;
}

.auth-tab {
  flex: 1;
  padding: 7px 0;
  border-radius: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-text-muted);
  background: transparent;
  transition: background 0.15s, color 0.15s;
}

.auth-tab.active {
  background: var(--color-surface);
  color: var(--color-text);
  box-shadow: 0 1px 4px rgba(0, 0, 0, 0.08);
}

/* Form */
.auth-form {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 5px;
}

.field label {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-muted);
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.field input {
  padding: 10px 13px;
  border: 1.5px solid var(--color-border);
  border-radius: 8px;
  font-size: 14px;
  color: var(--color-text);
  background: var(--color-bg);
  transition: border-color 0.15s;
  width: 100%;
}

.field input:focus {
  border-color: var(--color-primary);
  outline: none;
}

.auth-error {
  padding: 9px 12px;
  background: #fef2f2;
  border: 1px solid #fecaca;
  border-radius: 7px;
  font-size: 13px;
  color: #dc2626;
}

.btn-submit {
  margin-top: 4px;
  padding: 11px;
  background: var(--color-primary);
  color: #fff;
  border-radius: 8px;
  font-size: 14px;
  font-weight: 600;
  transition: background 0.15s;
  width: 100%;
}

.btn-submit:hover:not(:disabled) {
  background: var(--color-primary-hover);
}

.btn-submit:disabled {
  opacity: 0.55;
  cursor: not-allowed;
}
</style>
