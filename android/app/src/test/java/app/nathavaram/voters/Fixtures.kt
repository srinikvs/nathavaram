package app.nathavaram.voters

import app.nathavaram.voters.store.VoterEntity
import app.nathavaram.voters.sync.VoterDto

/** Synthetic test data only: fake names, mobiles 90000000xx, voter ids ZZZ00000xx. */
object Fixtures {
    const val VOTER_1 = "ZZZ0000001"
    const val VOTER_2 = "ZZZ0000002"
    const val VOTER_3 = "ZZZ0000003"

    const val T1 = "2026-11-11T10:00:00.000000Z"
    const val T2 = "2026-11-11T11:00:00.000000Z"
    const val T3 = "2026-11-11T12:00:00.000000Z"

    fun entity(
        voterId: String = VOTER_1,
        firstName: String = "Testperson",
        lastName: String? = "One",
        updatedAt: String = T1,
        revision: Long = 0,
        dirty: Boolean = false,
        party: String? = null,
        mobile: String? = "9000000001",
        houseNo: String? = "12-A",
        street: String? = "Main Street",
        village: String? = null,
        isLocal: Boolean = false,
        caste: String? = null,
    ) = VoterEntity(
        voterId = voterId, firstName = firstName, lastName = lastName,
        houseNo = houseNo, street = street, village = village, isLocal = isLocal, caste = caste,
        mobile = mobile, party = party, age = 42,
        createdAt = T1, updatedAt = updatedAt, revision = revision, dirty = dirty,
    )

    fun dto(
        voterId: String = VOTER_1,
        firstName: String = "Testperson",
        lastName: String? = "One",
        updatedAt: String = T2,
        revision: Long = 5,
        party: String? = null,
        deletedAt: String? = null,
    ) = VoterDto(
        voterId = voterId, firstName = firstName, lastName = lastName,
        houseNo = "12-A", street = "Main Street", age = 42, party = party, mobile = "9000000001",
        createdAt = T1, updatedAt = updatedAt,
        revision = revision, deletedAt = deletedAt, sourceId = "android-test",
    )

    /** What the server sends for a deleted row: personal fields stripped, the key kept. */
    fun tombstoneDto(voterId: String = VOTER_1, revision: Long = 7, updatedAt: String = T3) = VoterDto(
        voterId = voterId, firstName = "",
        createdAt = T1, updatedAt = updatedAt, revision = revision, deletedAt = updatedAt,
    )
}
