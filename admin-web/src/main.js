import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
import { applyPwaManifest } from './utils/pwaManifest'
import './styles/theme.css'
import '@vuepic/vue-datepicker/dist/main.css'

applyPwaManifest(window.location.pathname)

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')
