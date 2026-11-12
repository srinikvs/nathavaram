package app.nathavaram.voters.domain

import app.nathavaram.voters.store.VoterEntity

/** Snake_case keys used for form fields and validation errors. */
object FieldKey {
    const val BOOTH = "booth"
    const val BS_NO_2024 = "bs_no_2024"
    const val WARD = "ward"
    const val WARD_S_NO = "ward_s_no"
    const val VOTER_ID = "voter_id"
    const val FIRST_NAME = "first_name"
    const val SURNAME = "surname"
    const val RELATIVE_NAME = "relative_name"
    const val ADDRESS = "address"
    const val VILLAGE = "village"
    const val PERMANENT_ADDRESS = "permanent_address"
    const val IS_LOCAL = "is_local"
    const val GENDER = "gender"
    const val AGE = "age"
    const val CASTE = "caste"
    const val MOBILE = "mobile"
    const val C_CITY = "c_city"
    const val PROFESSION = "profession"
    const val PARTY = "party"

    /**
     * The only fields the regular "user" role may change on an existing voter (same list as the
     * server's USER_EDITABLE_FIELDS). Owner/admin may change every field except [VOTER_ID].
     */
    val USER_EDITABLE: Set<String> = setOf(PARTY, CASTE, MOBILE, ADDRESS)
}

/** Editable voter fields as the operator typed them. Immutable; every change returns a copy. */
data class VoterForm(
    val booth: String = "",
    val bsNo2024: String = "",
    val ward: String = "",
    val wardSNo: String = "",
    val voterId: String = "",
    val firstName: String = "",
    val surname: String = "",
    val relativeName: String = "",
    val address: String = "",
    val village: String = "",
    val permanentAddress: String = "",
    /** "Yes" or "No". */
    val isLocal: String = Vocab.NO,
    val gender: String = Vocab.DEFAULT_GENDER,
    val age: String = "",
    val caste: String = "",
    val mobile: String = "",
    val cCity: String = "",
    val profession: String = "",
    val party: String = "",
) {
    fun get(key: String): String = when (key) {
        FieldKey.BOOTH -> booth
        FieldKey.BS_NO_2024 -> bsNo2024
        FieldKey.WARD -> ward
        FieldKey.WARD_S_NO -> wardSNo
        FieldKey.VOTER_ID -> voterId
        FieldKey.FIRST_NAME -> firstName
        FieldKey.SURNAME -> surname
        FieldKey.RELATIVE_NAME -> relativeName
        FieldKey.ADDRESS -> address
        FieldKey.VILLAGE -> village
        FieldKey.PERMANENT_ADDRESS -> permanentAddress
        FieldKey.IS_LOCAL -> isLocal
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
        FieldKey.BOOTH -> copy(booth = value)
        FieldKey.BS_NO_2024 -> copy(bsNo2024 = value)
        FieldKey.WARD -> copy(ward = value)
        FieldKey.WARD_S_NO -> copy(wardSNo = value)
        FieldKey.VOTER_ID -> copy(voterId = value)
        FieldKey.FIRST_NAME -> copy(firstName = value)
        FieldKey.SURNAME -> copy(surname = value)
        FieldKey.RELATIVE_NAME -> copy(relativeName = value)
        FieldKey.ADDRESS -> copy(address = value)
        FieldKey.VILLAGE -> copy(village = value)
        FieldKey.PERMANENT_ADDRESS -> copy(permanentAddress = value)
        FieldKey.IS_LOCAL -> copy(isLocal = value)
        FieldKey.GENDER -> copy(gender = value)
        FieldKey.AGE -> copy(age = value)
        FieldKey.CASTE -> copy(caste = value)
        FieldKey.MOBILE -> copy(mobile = value)
        FieldKey.C_CITY -> copy(cCity = value)
        FieldKey.PROFESSION -> copy(profession = value)
        FieldKey.PARTY -> copy(party = value)
        else -> this
    }

    /**
     * What a regular user may submit for an existing voter: this form's Party, Caste, Mobile and
     * Address on top of [stored]. Every other field, including VoterId, is taken from [stored], so a
     * restricted field can never change (and never reaches the sync payload) from this device.
     */
    fun restrictedTo(stored: VoterForm): VoterForm =
        FieldKey.USER_EDITABLE.fold(stored) { acc, key -> acc.with(key, get(key)) }

    /** Flat list of every field in declaration order, for instance-state saving (rotation). */
    fun toSavedList(): List<String> = listOf(
        booth, bsNo2024, ward, wardSNo, voterId, firstName, surname, relativeName,
        address, village, permanentAddress, isLocal, gender, age, caste, mobile, cCity,
        profession, party,
    )

    companion object {
        /** Inverse of [toSavedList]; returns null if the list is not a form this version wrote. */
        fun fromSavedList(list: List<*>): VoterForm? {
            if (list.size != FIELD_COUNT || list.any { it !is String }) return null
            val s = list.map { it as String }
            return VoterForm(
                booth = s[0], bsNo2024 = s[1], ward = s[2], wardSNo = s[3],
                voterId = s[4], firstName = s[5], surname = s[6], relativeName = s[7],
                address = s[8], village = s[9], permanentAddress = s[10], isLocal = s[11],
                gender = s[12], age = s[13], caste = s[14], mobile = s[15],
                cCity = s[16], profession = s[17], party = s[18],
            )
        }

        private const val FIELD_COUNT = 19

        fun from(e: VoterEntity): VoterForm = VoterForm(
            booth = e.booth.orEmpty(),
            bsNo2024 = e.bsNo2024.orEmpty(),
            ward = e.ward.orEmpty(),
            wardSNo = e.wardSNo.orEmpty(),
            voterId = e.voterId,
            firstName = e.firstName,
            surname = e.surname.orEmpty(),
            relativeName = e.relativeName.orEmpty(),
            address = e.address.orEmpty(),
            village = e.village.orEmpty(),
            permanentAddress = e.permanentAddress.orEmpty(),
            isLocal = if (e.isLocal) Vocab.YES else Vocab.NO,
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
