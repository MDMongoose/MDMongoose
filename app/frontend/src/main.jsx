import React from 'react'
import { createRoot } from 'react-dom/client'
import App from './core/App.jsx'
import { installBusyButtons } from './core/busyButtons.js'
import './styles.css'

installBusyButtons()
createRoot(document.getElementById('root')).render(<App />)
