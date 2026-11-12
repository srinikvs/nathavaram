package app.nathavaram.voters.store

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import org.junit.Assert.assertEquals
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
    fun `fresh install uses the default server address`() {
        assertEquals("https://nathavaram.duckdns.org", store().baseUrl)
    }

    @Test
    fun `a saved address overrides the default`() {
        val settings = store()

        settings.baseUrl = "https://voters.example"

        assertEquals("https://voters.example", settings.baseUrl)
    }
}
