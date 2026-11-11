package app.nathavaram.voters.domain

import app.nathavaram.voters.data.VoterEntity

/** Snake_case keys used for form fields and validation errors. */
object FieldKey {
    const val BOOTH_2024 = "booth_2024"
    const val BS_NO_2024 = "bs_no_2024"
    const val WARD_2026 = "ward_2026"
    const val WARD_S_NO = "ward_s_no"
    const val VOTER_ID = "voter_id"
    const val FIRST_NAME = "first_name"
    const val SURNAME = "surname"
    const val RELATIVE_NAME = "relative_name"
    const val HOUSE_NO = "house_no"
    const val GENDER = "gender"
    const val AGE = "age"
    const val CASTE = "caste"
    const val MOBILE = "mobile"
    const val C_CITY = "c_city"
    const val PROFESSION = "profession"
    const val PARTY = "party"
}

/** Editable voter fields as the operator typed them. Immutable; every change returns a copy. */
data class VoterForm(
    val booth2024: String = "",
    val bsNo2024: String = "",
    val ward2026: String = "",
    val wardSNo: String = "",
    val voterId: String = "",
    val firstName: String = "",
    val surname: String = "",
    val relativeName: String = "",
    val houseNo: String = "",
    val gender: String = Vocab.DEFAULT_GENDER,
    val age: String = "",
    val caste: String = "",
    val mobile: String = "",
    val cCity: String = "",
    val profession: String = "",
    val party: String = "",
) {
    fun get(key: String): String = when (key) {
        FieldKey.BOOTH_2024 -> booth2024
        FieldKey.BS_NO_2024 -> bsNo2024
        FieldKey.WARD_2026 -> ward2026
        FieldKey.WARD_S_NO -> wardSNo
        FieldKey.VOTER_ID -> voterId
        FieldKey.FIRST_NAME -> firstName
        FieldKey.SURNAME -> surname
        FieldKey.RELATIVE_NAME -> relativeName
        FieldKey.HOUSE_NO -> houseNo
        FieldKey.GENDER -> gender
        FieldKey.AGE -> age
        FieldKey.CASTE -> caste
        FieldKey.MOBILE -> mobile
        FieldKey.C_CITY -> cCity
        FieldKey.PROFESSION -> profession
        FieldKey.PARTY -> party
        else -> ""
    }

    fun with(key: String, value: String): VoterForm = when (key) {
        FieldKey.BOOTH_2024 -> copy(booth2024 = value)
        FieldKey.BS_NO_2024 -> copy(bsNo2024 = value)
        FieldKey.WARD_2026 -> copy(ward2026 = value)
        FieldKey.WARD_S_NO -> copy(wardSNo = value)
        FieldKey.VOTER_ID -> copy(voterId = value)
        FieldKey.FIRST_NAME -> copy(firstName = value)
        FieldKey.SURNAME -> copy(surname = value)
        FieldKey.RELATIVE_NAME -> copy(relativeName = value)
        FieldKey.HOUSE_NO -> copy(houseNo = value)
        FieldKey.GENDER -> copy(gender = value)
        FieldKey.AGE -> copy(age = value)
        FieldKey.CASTE -> copy(caste = value)
        FieldKey.MOBILE -> copy(mobile = value)
        FieldKey.C_CITY -> copy(cCity = value)
        FieldKey.PROFESSION -> copy(profession = value)
        FieldKey.PARTY -> copy(party = value)
        else -> this
    }

    companion object {
        fun from(e: VoterEntity): VoterForm = VoterForm(
            booth2024 = e.booth2024.orEmpty(),
            bsNo2024 = e.bsNo2024.orEmpty(),
            ward2026 = e.ward2026.orEmpty(),
            wardSNo = e.wardSNo.orEmpty(),
            voterId = e.voterId,
            firstName = e.firstName,
            surname = e.surname.orEmpty(),
            relativeName = e.relativeName.orEmpty(),
            houseNo = e.houseNo.orEmpty(),
            gender = e.gender,
            age = e.age?.toString().orEmpty(),
            caste = e.caste.orEmpty(),
            mobile = e.mobile.orEmpty(),
            cCity = e.cCity.orEmpty(),
            profession = e.profession.orEmpty(),
            party = e.party.orEmpty(),
        )
    }
}
