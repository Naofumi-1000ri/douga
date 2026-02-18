import { useInView } from '@/hooks/useInView'
import { useAuthStore } from '@/store/authStore'
import { Link } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import LanguageSwitcher from '@/components/common/LanguageSwitcher'

function Section({ children, className = '', id }: { children: React.ReactNode; className?: string; id?: string }) {
  const { ref, inView } = useInView(0.12)
  return (
    <section ref={ref} id={id} className={`lp-section ${inView ? 'in-view' : ''} ${className}`}>
      {children}
    </section>
  )
}

/* ─── NavBar ─── */
function NavBar() {
  const { user } = useAuthStore()
  const { t } = useTranslation('dashboard')
  return (
    <nav className="fixed top-0 inset-x-0 z-50 bg-gray-950/80 backdrop-blur-md border-b border-gray-800/50">
      <div className="max-w-6xl mx-auto flex items-center justify-between px-6 h-14">
        <a href="#hero" className="text-xl font-bold tracking-tight text-white">
          atsurae
        </a>
        <div className="flex items-center gap-4">
          <a href="#features" className="hidden sm:block text-sm text-gray-400 hover:text-white">
            {t('nav.features')}
          </a>
          <a href="#pricing" className="hidden sm:block text-sm text-gray-400 hover:text-white">
            {t('nav.pricing')}
          </a>
          <LanguageSwitcher />
          {user ? (
            <Link
              to="/app"
              className="px-4 py-1.5 text-sm font-medium text-white bg-primary-600 hover:bg-primary-500 rounded-lg"
            >
              {t('nav.dashboard')}
            </Link>
          ) : (
            <Link
              to="/login"
              className="px-4 py-1.5 text-sm font-medium text-white bg-primary-600 hover:bg-primary-500 rounded-lg"
            >
              {t('nav.signIn')}
            </Link>
          )}
        </div>
      </div>
    </nav>
  )
}

/* ─── Hero ─── */
function Hero() {
  const { user } = useAuthStore()
  const { t } = useTranslation('dashboard')
  return (
    <section
      id="hero"
      className="relative min-h-screen flex items-center justify-center overflow-hidden"
    >
      {/* Nebula background */}
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{ backgroundImage: 'url(/lp/nebula_bg.webp)' }}
      />
      <div className="absolute inset-0 bg-gray-950/70" />

      <div className="relative z-10 text-center px-6 max-w-3xl animate-fade-in-up">
        <h1 className="text-6xl md:text-8xl font-black tracking-tight text-white mb-4">
          atsurae
        </h1>
        <p className="text-xl md:text-2xl text-gray-300 mb-10">
          {t('hero.tagline')}
        </p>
        <Link
          to={user ? '/app' : '/login'}
          className="inline-block px-8 py-3.5 text-lg font-semibold text-white bg-primary-600 hover:bg-primary-500 rounded-xl shadow-lg shadow-primary-600/25 transition-all hover:shadow-primary-500/30"
        >
          {user ? t('hero.ctaDashboard') : t('hero.ctaStart')}
        </Link>
      </div>
    </section>
  )
}

/* ─── Demo ─── */
function Demo() {
  const { t } = useTranslation('dashboard')
  return (
    <Section className="py-24 px-6">
      <div className="max-w-4xl mx-auto text-center">
        <h2 className="text-3xl md:text-4xl font-bold text-white mb-4">
          {t('demo.title')}
        </h2>
        <p className="text-gray-400 mb-10">
          {t('demo.subtitle')}
        </p>
        <div className="relative rounded-2xl overflow-hidden border border-gray-700/50 shadow-2xl">
          <video
            src="/lp/lp_video.mp4"
            controls
            playsInline
            preload="metadata"
            poster="/og-image.jpg"
            className="w-full aspect-video bg-gray-950"
          />
        </div>
      </div>
    </Section>
  )
}

/* ─── Features ─── */
function Features() {
  const { t } = useTranslation('dashboard')
  const featureTexts = t('features.items', { returnObjects: true }) as unknown as Array<{title: string; desc: string}>
  const icons = [
    (
      <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456Z" />
      </svg>
    ),
    (
      <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 6.878V6a2.25 2.25 0 0 1 2.25-2.25h7.5A2.25 2.25 0 0 1 18 6v.878m-12 0c.235-.083.487-.128.75-.128h10.5c.263 0 .515.045.75.128m-12 0A2.25 2.25 0 0 0 4.5 9v.878m13.5-3A2.25 2.25 0 0 1 19.5 9v.878m-15 0A2.244 2.244 0 0 0 3 12v4.5A2.25 2.25 0 0 0 5.25 19.5h13.5A2.25 2.25 0 0 0 21 17.25V12c0-.642-.27-1.222-.698-1.632m-13.104 0A2.244 2.244 0 0 1 9 12c0 .642.27 1.222.698 1.632m0 0A2.244 2.244 0 0 1 12 12c0-.642.27-1.222.698-1.632m0 0c.428.41.698.99.698 1.632 0 .642-.27 1.222-.698 1.632M12 12c0 .642.27 1.222.698 1.632m0 0c.428.41.698.99.698 1.632m-2.094-3.264A2.244 2.244 0 0 0 9 12c0 .642.27 1.222.698 1.632" />
      </svg>
    ),
    (
      <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" />
      </svg>
    ),
  ]
  const featureItems = featureTexts.map((item, i) => ({ ...item, icon: icons[i] }))

  return (
    <Section className="py-24 px-6 bg-gray-950/50" id="features">
      <div className="max-w-6xl mx-auto">
        <h2 className="text-3xl md:text-4xl font-bold text-white text-center mb-14">
          {t('features.title')}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 lp-stagger">
          {featureItems.map((f) => (
            <div
              key={f.title}
              className="lp-section in-view bg-gray-800/50 border border-gray-700/50 rounded-2xl p-8 hover:border-primary-600/40 transition-colors"
            >
              <div className="text-primary-400 mb-4">{f.icon}</div>
              <h3 className="text-xl font-semibold text-white mb-2">{f.title}</h3>
              <p className="text-gray-400 leading-relaxed">{f.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </Section>
  )
}

/* ─── How It Works ─── */
function HowItWorks() {
  const { t } = useTranslation('dashboard')
  const stepTexts = t('howItWorks.steps', { returnObjects: true }) as unknown as Array<{title: string; desc: string}>
  const steps = stepTexts.map((s, i) => ({ num: String(i + 1), ...s }))

  return (
    <Section className="py-24 px-6">
      <div className="max-w-4xl mx-auto">
        <h2 className="text-3xl md:text-4xl font-bold text-white text-center mb-14">
          {t('howItWorks.title')}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 lp-stagger">
          {steps.map((s) => (
            <div key={s.num} className="lp-section in-view text-center">
              <div className="w-14 h-14 mx-auto rounded-full bg-primary-600/20 border border-primary-500/30 flex items-center justify-center text-2xl font-bold text-primary-400 mb-4">
                {s.num}
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">{s.title}</h3>
              <p className="text-gray-400">{s.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </Section>
  )
}

/* ─── Pricing ─── */
function Pricing() {
  const { user } = useAuthStore()
  const { t } = useTranslation('dashboard')
  const freeFeatures = t('pricing.free.features', { returnObjects: true }) as unknown as string[]
  const proFeatures = t('pricing.pro.features', { returnObjects: true }) as unknown as string[]

  return (
    <Section className="py-24 px-6 bg-gray-950/50" id="pricing">
      <div className="max-w-4xl mx-auto">
        <h2 className="text-3xl md:text-4xl font-bold text-white text-center mb-14">
          {t('pricing.title')}
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 max-w-2xl mx-auto">
          {/* Free */}
          <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-8">
            <h3 className="text-lg font-semibold text-white mb-1">{t('pricing.free.name')}</h3>
            <p className="text-4xl font-bold text-white mb-6">
              {t('pricing.free.price')} <span className="text-base font-normal text-gray-500">{t('pricing.free.period')}</span>
            </p>
            <ul className="space-y-3 text-gray-300 text-sm mb-8">
              {freeFeatures.map((feature) => (
                <li key={feature} className="flex items-start gap-2">
                  <span className="text-primary-400 mt-0.5">&#10003;</span>
                  {feature}
                </li>
              ))}
            </ul>
            <Link
              to={user ? '/app' : '/login'}
              className="block w-full text-center px-6 py-2.5 text-sm font-semibold text-white bg-primary-600 hover:bg-primary-500 rounded-xl"
            >
              {user ? t('pricing.free.ctaDashboard') : t('pricing.free.cta')}
            </Link>
          </div>

          {/* Pro */}
          <div className="bg-gray-800/50 border border-gray-700/50 rounded-2xl p-8 relative opacity-60">
            <div className="absolute top-4 right-4 text-xs bg-gray-700 text-gray-300 px-2.5 py-1 rounded-full">
              Coming Soon
            </div>
            <h3 className="text-lg font-semibold text-white mb-1">{t('pricing.pro.name')}</h3>
            <p className="text-4xl font-bold text-white mb-6">
              {t('pricing.pro.price')} <span className="text-base font-normal text-gray-500">{t('pricing.pro.period')}</span>
            </p>
            <ul className="space-y-3 text-gray-300 text-sm mb-8">
              {proFeatures.map((feature) => (
                <li key={feature} className="flex items-start gap-2">
                  <span className="text-primary-400 mt-0.5">&#10003;</span>
                  {feature}
                </li>
              ))}
            </ul>
            <button
              disabled
              className="block w-full text-center px-6 py-2.5 text-sm font-semibold text-gray-400 bg-gray-700 rounded-xl cursor-not-allowed"
            >
              {t('pricing.pro.cta')}
            </button>
          </div>
        </div>
      </div>
    </Section>
  )
}

/* ─── Bottom CTA ─── */
function BottomCTA() {
  const { user } = useAuthStore()
  const { t } = useTranslation('dashboard')
  return (
    <section className="relative py-28 px-6 overflow-hidden">
      <div
        className="absolute inset-0 bg-cover bg-center"
        style={{ backgroundImage: 'url(/lp/nebula_bg.webp)' }}
      />
      <div className="absolute inset-0 bg-gray-950/70" />

      <div className="relative z-10 text-center max-w-2xl mx-auto">
        <h2 className="text-3xl md:text-5xl font-bold text-white mb-6">
          {t('cta.title')}
        </h2>
        <p className="text-gray-300 mb-10">
          {t('cta.subtitle')}
        </p>
        <Link
          to={user ? '/app' : '/login'}
          className="inline-block px-8 py-3.5 text-lg font-semibold text-white bg-primary-600 hover:bg-primary-500 rounded-xl shadow-lg shadow-primary-600/25 transition-all hover:shadow-primary-500/30"
        >
          {user ? t('cta.buttonDashboard') : t('cta.button')}
        </Link>
      </div>
    </section>
  )
}

/* ─── Footer ─── */
function Footer() {
  const { t } = useTranslation('dashboard')
  return (
    <footer className="border-t border-gray-800/50 py-8 px-6">
      <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4 text-sm text-gray-500">
        <span>{t('footer.rights')}</span>
        <div className="flex gap-6">
          <a href="#features" className="hover:text-gray-300">{t('nav.features')}</a>
          <a href="#pricing" className="hover:text-gray-300">{t('nav.pricing')}</a>
        </div>
      </div>
    </footer>
  )
}

/* ─── Page ─── */
export default function LandingPage() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <NavBar />
      <Hero />
      <Demo />
      <Features />
      <HowItWorks />
      <Pricing />
      <BottomCTA />
      <Footer />
    </div>
  )
}
