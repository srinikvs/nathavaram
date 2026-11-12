package app.nathavaram.voters.store

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import app.nathavaram.voters.domain.BaseUrl
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

// Use a plain Application so VotersApp.onCreate does not touch an uninitialised WorkManager.
@RunWith(RobolectricTestRunner::class)
@Config(application = Application::class)
class SettingsStoreTest {
    private fun store() = SettingsStore(ApplicationProvider.getApplicationContext<Application>())

    @Test
    fun `fresh install resolves to the built-in default address`() {
        assertEquals("https://nathavaram.duckdns.org", store().baseUrl)
    }

    @Test
    fun `reading the default does not persist it`() {
        val settings = store()

        settings.baseUrl

        val prefs = ApplicationProvider.getApplicationContext<Application>()
            .getSharedPreferences("settings", android.content.Context.MODE_PRIVATE)
        assertFalse(prefs.contains("base_url"))
    }

    @Test
    fun `a blank saved address falls back to the default`() {
        val settings = store()

        settings.baseUrl = "  "

        assertEquals(BaseUrl.DEFAULT, settings.baseUrl)
    }

    @Test
    fun `a saved address overrides the default`() {
        val settings = store()

        settings.baseUrl = "https://voters.example"

        assertEquals("https://voters.example", settings.baseUrl)
    }
}
