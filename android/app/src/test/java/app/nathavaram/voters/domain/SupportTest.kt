package app.nathavaram.voters.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant

class VocabTest {
    @Test
    fun `party list is exactly the nine canonical values`() {
        assertEquals(
            listOf("TDP", "YSRCP", "Jana Sena", "BJP", "INC", "CPI", "Independent", "Unknown", "Prefer not to say"),
            Vocab.PARTIES,
        )
    }

    @Test
    fun `local options are exactly Non-Local and Local`() {
        assertEquals(listOf("Non-Local", "Local"), Vocab.LOCAL_OPTIONS)
    }
}

class BaseUrlTest {
    private fun ok(input: String, cleartext: Boolean) =
        (BaseUrl.normalize(input, cleartext) as BaseUrl.Result.Ok).url

    @Test
    fun `https address is accepted and a trailing slash is removed`() {
        assertEquals("https://voters.example", ok(" https://voters.example/ ", cleartext = false))
    }

    @Test
    fun `http is refused unless cleartext is allowed (debug builds)`() {
        assertTrue(BaseUrl.normalize("http://10.0.2.2:8000", allowCleartext = false) is BaseUrl.Result.Invalid)
        assertEquals("http://10.0.2.2:8000", ok("http://10.0.2.2:8000", cleartext = true))
    }

    @Test
    fun `empty, scheme-less and query addresses are refused`() {
        listOf("", "   ", "voters.example", "ftp://voters.example", "https://voters.example/?a=1").forEach {
            assertTrue("input '$it'", BaseUrl.normalize(it, allowCleartext = true) is BaseUrl.Result.Invalid)
        }
    }

    @Test
    fun `trailing slashes are trimmed from an https address`() {
        assertEquals("https://voters.example", ok("https://voters.example//", cleartext = false))
    }

    @Test
    fun `a path prefix is kept`() {
        assertEquals("https://voters.example/app", ok("https://voters.example/app/", cleartext = false))
    }

    @Test
    fun `default address is exactly the production host with no trailing slash`() {
        assertEquals("https://nathavaram.duckdns.org", BaseUrl.DEFAULT)
        assertFalse(BaseUrl.DEFAULT.endsWith("/"))
    }

    @Test
    fun `default address passes its own validation unchanged`() {
        assertEquals(BaseUrl.DEFAULT, ok(BaseUrl.DEFAULT, cleartext = false))
    }
}

class TimestampsTest {
    @Test
    fun `format uses six fractional digits and a Z suffix`() {
        val text = Timestamps.format(Instant.parse("2026-11-11T18:04:00.123456Z"))

        assertEquals("2026-11-11T18:04:00.123456Z", text)
    }

    @Test
    fun `a value without a timezone is read as UTC`() {
        assertEquals(Instant.parse("2026-11-11T18:04:00Z"), Timestamps.parse("2026-11-11T18:04:00"))
        assertEquals(Instant.parse("2026-11-11T12:34:00Z"), Timestamps.parse("2026-11-11T18:04:00+05:30"))
        assertNull(Timestamps.parse("not a time"))
    }

    @Test
    fun `isNewer compares instants, not text`() {
        assertTrue(Timestamps.isNewer("2026-11-11T18:04:00.000001Z", "2026-11-11T18:04:00.000000Z"))
        assertFalse(Timestamps.isNewer("2026-11-11T18:04:00Z", "2026-11-11T18:04:00.000000Z"))
        assertTrue(Timestamps.isNewer("2026-11-11T18:04:00+00:00", "2026-11-11T18:03:59Z"))
    }

    @Test
    fun `now parses back to a recent instant`() {
        assertNotNull(Timestamps.parse(Timestamps.now()))
    }

    @Test
    fun `clock skew is flagged past five minutes either way`() {
        val server = "2026-11-11T12:00:00.000000Z"

        assertFalse(ClockSkew.exceeds(server, Instant.parse("2026-11-11T12:04:00Z")))
        assertTrue(ClockSkew.exceeds(server, Instant.parse("2026-11-11T12:06:00Z")))
        assertTrue(ClockSkew.exceeds(server, Instant.parse("2026-11-11T11:50:00Z")))
        assertFalse(ClockSkew.exceeds("garbage", Instant.parse("2026-11-11T11:50:00Z")))
    }
}
