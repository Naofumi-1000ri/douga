import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import LanguageDetector from 'i18next-browser-languagedetector'

import enCommon from './locales/en/common.json'
import enDashboard from './locales/en/dashboard.json'
import enEditor from './locales/en/editor.json'
import enAssets from './locales/en/assets.json'
import jaCommon from './locales/ja/common.json'
import jaDashboard from './locales/ja/dashboard.json'
import jaEditor from './locales/ja/editor.json'
import jaAssets from './locales/ja/assets.json'

i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    defaultNS: 'common',
    resources: {
      en: { common: enCommon, dashboard: enDashboard, editor: enEditor, assets: enAssets },
      ja: { common: jaCommon, dashboard: jaDashboard, editor: jaEditor, assets: jaAssets },
    },
    fallbackLng: 'en',
    supportedLngs: ['en', 'ja'],
    load: 'languageOnly',
    detection: {
      order: ['localStorage', 'navigator'],
      caches: ['localStorage'],
      lookupLocalStorage: 'douga-language',
    },
    interpolation: { escapeValue: false },
  })

export default i18n
