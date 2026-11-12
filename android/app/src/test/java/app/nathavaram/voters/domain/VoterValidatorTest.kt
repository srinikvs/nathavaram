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
        assertEquals("Unknown", form.gender)
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
    fun `gender is matched case-insensitively and blank becomes Unknown`() {
        assertEquals("Female", ok(valid().copy(gender = "fEMALE")).gender)
        assertEquals("Unknown", ok(valid().copy(gender = "")).gender)
        assertTrue(FieldKey.GENDER in errors(valid().copy(gender = "Robot")))
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
        assertTrue(FieldKey.SURNAME in errors(valid().copy(surname = "bad\u0007name")))
        assertTrue(FieldKey.SURNAME in errors(valid().copy(surname = "x".repeat(129))))
        assertTrue(FieldKey.ADDRESS in errors(valid().copy(address = "x".repeat(1001))))
        assertTrue(FieldKey.PERMANENT_ADDRESS in errors(valid().copy(permanentAddress = "x".repeat(1001))))
        assertTrue(FieldKey.VILLAGE in errors(valid().copy(village = "x".repeat(129))))
    }

    @Test
    fun `local accepts yes and no in any case and defaults to no`() {
        assertEquals("Yes", ok(valid().copy(isLocal = " yes ")).isLocal)
        assertEquals("No", ok(valid().copy(isLocal = "NO")).isLocal)
        assertEquals("No", ok(valid().copy(isLocal = "")).isLocal)
        assertEquals("No", ok(valid()).isLocal)
        assertTrue(FieldKey.IS_LOCAL in errors(valid().copy(isLocal = "maybe")))
    }

    @Test
    fun `village and permanent address are trimmed`() {
        val form = ok(valid().copy(village = "  Samplevillage ", permanentAddress = " Sample Lane 5 "))

        assertEquals("Samplevillage", form.village)
        assertEquals("Sample Lane 5", form.permanentAddress)
    }

    @Test
    fun `Telugu text is accepted`() {
        val form = ok(valid().copy(firstName = "పరీక్ష"))

        assertEquals("పరీక్ష", form.firstName)
    }
}
