package app.nathavaram.voters.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class VoterValidatorTest {
    private fun valid() = VoterForm(voterId = "zzz0000001", firstName = "  Testperson ")

    private fun ok(form: VoterForm) =
        (VoterValidator.validate(form) as VoterValidator.Result.Ok).form

    private fun errors(form: VoterForm) =
        (VoterValidator.validate(form) as VoterValidator.Result.Invalid).errors

    @Test
    fun `minimal form is accepted, trimmed and normalized`() {
        val form = ok(valid())

        assertEquals("ZZZ0000001", form.voterId)
        assertEquals("Testperson", form.firstName)
        assertEquals("Non-Local", form.isLocal)
        assertEquals("", form.party)
    }

    @Test
    fun `voter id must be 3 to 32 letters or digits`() {
        assertTrue(FieldKey.VOTER_ID in errors(valid().copy(voterId = "ab")))
        assertTrue(FieldKey.VOTER_ID in errors(valid().copy(voterId = "ZZZ-0001")))
        assertTrue(FieldKey.VOTER_ID in errors(valid().copy(voterId = "A".repeat(33))))
        ok(valid().copy(voterId = "A".repeat(32)))
    }

    @Test
    fun `first name is required`() {
        assertEquals("Required", errors(valid().copy(firstName = "  "))[FieldKey.FIRST_NAME])
    }

    @Test
    fun `there is no gender, permanent address or profession field any more`() {
        listOf("gender", "permanent_address", "profession").forEach { key ->
            assertEquals("", VoterForm().get(key))
            assertEquals(VoterForm(), VoterForm().with(key, "x"))
        }
    }

    @Test
    fun `every canonical party is accepted case-insensitively and stored canonical`() {
        Vocab.PARTIES.forEach { party ->
            assertEquals(party, ok(valid().copy(party = party.uppercase())).party)
            assertEquals(party, ok(valid().copy(party = party.lowercase())).party)
        }
    }

    @Test
    fun `empty party means no answer and an unknown party is rejected`() {
        assertEquals("", ok(valid().copy(party = "  ")).party)
        assertTrue(FieldKey.PARTY in errors(valid().copy(party = "Other Party")))
    }

    @Test
    fun `age is blank or a whole number from 1 to 130`() {
        ok(valid().copy(age = ""))
        ok(valid().copy(age = "1"))
        ok(valid().copy(age = "130"))
        listOf("0", "131", "-4", "abc", "4.5").forEach { bad ->
            assertTrue("age $bad", FieldKey.AGE in errors(valid().copy(age = bad)))
        }
    }

    @Test
    fun `mobile accepts Indian formats and keeps the text as entered`() {
        assertEquals("+91 90000-00012", ok(valid().copy(mobile = " +91 90000-00012 ")).mobile)
        ok(valid().copy(mobile = "09000000013"))
        ok(valid().copy(mobile = "9000000014"))
        listOf("1234567890", "90000", "abcdefghij", "90000000145").forEach { bad ->
            assertTrue("mobile $bad", FieldKey.MOBILE in errors(valid().copy(mobile = bad)))
        }
    }

    @Test
    fun `control characters and over-long text are rejected`() {
        assertTrue(FieldKey.LAST_NAME in errors(valid().copy(lastName = "bad\u0007name")))
        assertTrue(FieldKey.LAST_NAME in errors(valid().copy(lastName = "x".repeat(129))))
        assertTrue(FieldKey.STREET in errors(valid().copy(street = "x".repeat(1001))))
        assertTrue(FieldKey.HOUSE_NO in errors(valid().copy(houseNo = "x".repeat(65))))
        assertTrue(FieldKey.VILLAGE in errors(valid().copy(village = "x".repeat(129))))
        assertTrue(FieldKey.PANCHAYATH in errors(valid().copy(panchayath = "x".repeat(129))))
        assertTrue(FieldKey.MANDAL in errors(valid().copy(mandal = "x".repeat(129))))
        assertTrue(FieldKey.CURRENT_CITY in errors(valid().copy(currentCity = "x".repeat(129))))
    }

    @Test
    fun `local accepts local and non-local in any case and defaults to non-local`() {
        assertEquals("Local", ok(valid().copy(isLocal = " LOCAL ")).isLocal)
        assertEquals("Non-Local", ok(valid().copy(isLocal = "non-local")).isLocal)
        assertEquals("Local", ok(valid().copy(isLocal = "yes")).isLocal)
        assertEquals("Non-Local", ok(valid().copy(isLocal = "NO")).isLocal)
        assertEquals("Non-Local", ok(valid().copy(isLocal = "")).isLocal)
        assertEquals("Non-Local", ok(valid()).isLocal)
        assertTrue(FieldKey.IS_LOCAL in errors(valid().copy(isLocal = "maybe")))
    }

    @Test
    fun `village panchayath and mandal are trimmed`() {
        val form = ok(valid().copy(village = "  Samplevillage ", panchayath = " Pan ", mandal = " Man "))

        assertEquals("Samplevillage", form.village)
        assertEquals("Pan", form.panchayath)
        assertEquals("Man", form.mandal)
    }

    @Test
    fun `Telugu text is accepted`() {
        val form = ok(valid().copy(firstName = "పరీక్ష"))

        assertEquals("పరీక్ష", form.firstName)
    }
}
