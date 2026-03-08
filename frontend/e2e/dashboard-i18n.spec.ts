import { expect, test } from '@playwright/test'

const mockProjects = [
  {
    id: 'project-dashboard-i18n',
    name: 'Seeded Project',
    description: null,
    status: 'active',
    duration_ms: 120000,
    thumbnail_url: null,
    created_at: '2026-03-07T00:00:00.000Z',
    updated_at: '2026-03-07T00:00:00.000Z',
    is_shared: false,
    role: 'owner',
    owner_name: 'Dev User',
  },
]

test.describe('Dashboard i18n', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/projects', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(mockProjects),
      })
    })

    await page.route('**/api/members/invitations', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([]),
      })
    })
  })

  test('shows translated dashboard labels in Japanese without raw i18n keys', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('douga-language', 'ja')
    })

    await page.goto('/app')
    await page.waitForLoadState('networkidle')

    await expect(page.getByRole('heading', { name: 'ダッシュボード' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'プロジェクト' })).toBeVisible()
    await expect(page.getByRole('button', { name: '新規プロジェクト' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'ログアウト' })).toBeVisible()

    await expect(page.getByText('projects.sectionTitle')).toHaveCount(0)
    await expect(page.getByText('projects.newProject')).toHaveCount(0)
    await expect(page.getByText('header.signOut')).toHaveCount(0)
  })

  test('shows translated dashboard labels in English without raw i18n keys', async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('douga-language', 'en')
    })

    await page.goto('/app')
    await page.waitForLoadState('networkidle')

    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Projects' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'New Project' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Sign Out' })).toBeVisible()

    await expect(page.getByText('projects.sectionTitle')).toHaveCount(0)
    await expect(page.getByText('projects.newProject')).toHaveCount(0)
    await expect(page.getByText('header.signOut')).toHaveCount(0)
  })
})
