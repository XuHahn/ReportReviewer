import React from 'react'
import ReactDOM from 'react-dom/client'
import { createTheme, MantineProvider } from '@mantine/core'
import { Notifications } from '@mantine/notifications'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@mantine/core/styles.css'
import '@mantine/notifications/styles.css'
import './styles.css'
import App from './App'

const theme = createTheme({
  primaryColor: 'lab',
  colors: {
    lab: ['#edf8f5', '#d9eee8', '#b5ddd3', '#8bcabc', '#5eb3a3', '#369888', '#1f7c70', '#17645c', '#145149', '#0d3f39'],
  },
  fontFamily: '"PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", system-ui, sans-serif',
  headings: { fontFamily: '"Songti SC", "Noto Serif CJK SC", "STSong", serif', fontWeight: '600' },
  defaultRadius: 'sm',
  cursorType: 'pointer',
})

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 20_000, refetchOnWindowFocus: false } } })

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <MantineProvider theme={theme} defaultColorScheme="auto">
        <Notifications position="top-right" limit={4} />
        <App />
      </MantineProvider>
    </QueryClientProvider>
  </React.StrictMode>,
)
