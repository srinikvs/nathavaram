package app.nathavaram.voters

import app.nathavaram.voters.store.VoterEntity
import app.nathavaram.voters.sync.VoterDto

/** Synthetic test data only: fake names, mobiles 90000000xx, voter ids ZZZ00000xx. */
object Fixtures {
    const val ID_1 = "00000000-0000-4000-8000-000000000001"
    const val ID_2 = "00000000-0000-4000-8000-000000000002"
    const val ID_3 = "00000000-0000-4000-8000-000000000003"

    const val T1 = "2026-11-11T10:00:00.000000Z"
    const val T2 = "2026-11-11T11:00:00.000000Z"
    const val T3 = "2026-11-11T12:00:00.000000Z"

    fun entity(
        id: String = ID_1,
        voterId: String = "ZZZ0000001",
        firstName: String = "Testperson",
        surname: String? = "One",
        updatedAt: String = T1,
        revision: Long = 0,
        dirty: Boolean = false,
        party: String? = null,
        mobile: String? = "9000000001",
        houseNo: String? = "12-A",
    ) = VoterEntity(
        id = id, voterId = voterId, firstName = firstName, surname = surname,
        houseNo = houseNo, mobile = mobile, party = party, gender = "Male", age = 42,
        createdAt = T1, updatedAt = updatedAt, revision = revision, dirty = dirty,
    )

    fun dto(
        id: String = ID_1,
        voterId: String = "ZZZ0000001",
        firstName: String = "Testperson",
        surname: String? = "One",
        updatedAt: String = T2,
        revision: Long = 5,
        party: String? = null,
        deletedAt: String? = null,
    ) = VoterDto(
        id = id, voterId = voterId, firstName = firstName, surname = surname, gender = "Male",
        houseNo = "12-A", age = 42, party = party, mobile = "9000000001", createdAt = T1, updatedAt = updatedAt,
        revision = revision, deletedAt = deletedAt, sourceId = "android-test",
    )

    /** What the server sends for a deleted row: personal fields stripped, inert placeholders. */
    fun tombstoneDto(id: String = ID_1, revision: Long = 7, updatedAt: String = T3) = VoterDto(
        id = id, voterId = id.replace("-", ""), firstName = "", gender = "Unknown",
        createdAt = T1, updatedAt = updatedAt, revision = revision, deletedAt = updatedAt,
    )
}
