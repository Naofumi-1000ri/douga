import { useTranslation } from 'react-i18next'

interface LanguageSwitcherProps {
  className?: string
}

export default function LanguageSwitcher({ className = '' }: LanguageSwitcherProps) {
  const { i18n, t } = useTranslation()
  const isJa = i18n.language === 'ja' || i18n.language.startsWith('ja')

  const toggle = () => {
    i18n.changeLanguage(isJa ? 'en' : 'ja')
  }

  return (
    <button
      onClick={toggle}
      className={`text-sm text-gray-400 hover:text-white transition-colors px-2 py-1 rounded ${className}`}
      title={isJa ? t('lang.switchToEn') : t('lang.switchToJa')}
    >
      {isJa ? 'EN' : 'JA'}
    </button>
  )
}
