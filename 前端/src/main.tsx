import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'
import './styles/holographic.css'
import './styles/art-direction.css'
import './styles/mist-theme.css'
import './styles/owner-account.css'
import './styles/runtime-version.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
