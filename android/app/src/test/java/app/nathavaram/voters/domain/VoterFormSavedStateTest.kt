package app.nathavaram.voters.domain

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class VoterFormSavedStateTest {
    private val typed = VoterForm(
        voterId = "ZZZ0000001",
        firstName = "Testperson",
        surname = "Fakeuser",
        address = "H.No 12-A, Sample Street",
        village = "Samplevillage",
        permanentAddress = "Sample Lane 5",
        isLocal = "Yes",
        gender = "Female",
        age = "41",
        mobile = "9000000001",
        party = "Independent",
    )

    @Test
    fun `saved list round-trips every field`() {
        val restored = VoterForm.fromSavedList(typed.toSavedList())

        assertEquals(typed, restored)
    }

    @Test
    fun `round trip keeps unsaved gender and party selections`() {
        val form = VoterForm().with(FieldKey.GENDER, "Other").with(FieldKey.PARTY, "QA000001")

        val restored = VoterForm.fromSavedList(form.toSavedList())

        assertEquals("Other", restored?.gender)
        assertEquals("QA000001", restored?.party)
    }

    @Test
    fun `a list of the wrong size or type is rejected`() {
        assertNull(VoterForm.fromSavedList(listOf("a", "b")))
        assertNull(VoterForm.fromSavedList(List(19) { if (it == 3) 7 else "x" }))
    }
}
