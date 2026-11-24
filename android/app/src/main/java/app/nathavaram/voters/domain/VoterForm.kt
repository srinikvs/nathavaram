package app.nathavaram.voters.domain

import app.nathavaram.voters.store.VoterEntity

/** Snake_case keys used for form fields and validation errors. */
object FieldKey {
    const val BOOTH = "booth"
    const val BOOTH_S_NO = "booth_s_no"
    const val WARD = "ward"
    const val WARD_S_NO = "ward_s_no"
    const val VOTER_ID = "voter_id"
    const val FIRST_NAME = "first_name"
    const val LAST_NAME = "last_name"
    const val RELATIVE_NAME = "relative_name"
    const val HOUSE_NO = "house_no"
    const val AGE = "age"
    const val STREET = "street"
    const val MOBILE = "mobile"
    const val CASTE = "caste"
    const val VILLAGE = "village"
    const val PANCHAYATH = "panchayath"
    const val MANDAL = "mandal"
    const val CURRENT_CITY = "current_city"
    const val IS_LOCAL = "is_local"
    const val PARTY = "party"
    const val CONSTITUENCY = "constituency"

    /**
     * The only fields the "user" and "manager" roles may change on an existing voter. Same 14-field
     * list as the server's USER_EDITABLE_FIELDS (app/constants.py). Admin and owner may change every
     * field except [VOTER_ID]. Caste, FirstName, LastName, Gender, Mandal, Constituency stay locked.
     */
    val USER_EDITABLE: Set<String> = setOf(
        BOOTH, BOOTH_S_NO, WARD, WARD_S_NO, RELATIVE_NAME, HOUSE_NO, AGE, STREET, MOBILE,
        VILLAGE, PANCHAYATH, CURRENT_CITY, IS_LOCAL, PARTY,
    )
}

/** Editable voter fields as the operator typed them. Immutable; every change returns a copy. */
data class VoterForm(
    val booth: String = "",
    val boothSNo: String = "",
    val ward: String = "",
    val wardSNo: String = "",
    val voterId: String = "",
    val firstName: String = "",
    val lastName: String = "",
    val relativeName: String = "",
    val houseNo: String = "",
    val age: String = "",
    val street: String = "",
    val mobile: String = "",
    val caste: String = "",
    val village: String = "",
    val panchayath: String = "",
    val mandal: String = "",
    val currentCity: String = "",
    /** "Local" or "Non-Local". */
    val isLocal: String = Vocab.NON_LOCAL,
    val party: String = "",
    val constituency: String = "",
) {
    fun get(key: String): String = when (key) {
        FieldKey.BOOTH -> booth
        FieldKey.BOOTH_S_NO -> boothSNo
        FieldKey.WARD -> ward
        FieldKey.WARD_S_NO -> wardSNo
        FieldKey.VOTER_ID -> voterId
        FieldKey.FIRST_NAME -> firstName
        FieldKey.LAST_NAME -> lastName
        FieldKey.RELATIVE_NAME -> relativeName
        FieldKey.HOUSE_NO -> houseNo
        FieldKey.AGE -> age
        FieldKey.STREET -> street
        FieldKey.MOBILE -> mobile
        FieldKey.CASTE -> caste
        FieldKey.VILLAGE -> village
        FieldKey.PANCHAYATH -> panchayath
        FieldKey.MANDAL -> mandal
        FieldKey.CURRENT_CITY -> currentCity
        FieldKey.IS_LOCAL -> isLocal
        FieldKey.PARTY -> party
        FieldKey.CONSTITUENCY -> constituency
        else -> ""
    }

    fun with(key: String, value: String): VoterForm = when (key) {
        FieldKey.BOOTH -> copy(booth = value)
        FieldKey.BOOTH_S_NO -> copy(boothSNo = value)
        FieldKey.WARD -> copy(ward = value)
        FieldKey.WARD_S_NO -> copy(wardSNo = value)
        FieldKey.VOTER_ID -> copy(voterId = value)
        FieldKey.FIRST_NAME -> copy(firstName = value)
        FieldKey.LAST_NAME -> copy(lastName = value)
        FieldKey.RELATIVE_NAME -> copy(relativeName = value)
        FieldKey.HOUSE_NO -> copy(houseNo = value)
        FieldKey.AGE -> copy(age = value)
        FieldKey.STREET -> copy(street = value)
        FieldKey.MOBILE -> copy(mobile = value)
        FieldKey.CASTE -> copy(caste = value)
        FieldKey.VILLAGE -> copy(village = value)
        FieldKey.PANCHAYATH -> copy(panchayath = value)
        FieldKey.MANDAL -> copy(mandal = value)
        FieldKey.CURRENT_CITY -> copy(currentCity = value)
        FieldKey.IS_LOCAL -> copy(isLocal = value)
        FieldKey.PARTY -> copy(party = value)
        FieldKey.CONSTITUENCY -> copy(constituency = value)
        else -> this
    }

    /**
     * What a regular user may submit for an existing voter: this form's Party, Caste, Mobile,
     * HouseNo and Street on top of [stored]. Every other field, including VoterId, is taken from
     * [stored], so a restricted field can never change (and never reaches the sync payload) from
     * this device.
     */
    fun restrictedTo(stored: VoterForm): VoterForm =
        FieldKey.USER_EDITABLE.fold(stored) { acc, key -> acc.with(key, get(key)) }

    /** Flat list of every field in declaration order, for instance-state saving (rotation). */
    fun toSavedList(): List<String> = listOf(
        booth, boothSNo, ward, wardSNo, voterId, firstName, lastName, relativeName, houseNo,
        age, street, mobile, caste, village, panchayath, mandal, currentCity, isLocal, party, constituency,
    )

    companion object {
        /** Inverse of [toSavedList]; returns null if the list is not a form this version wrote. */
        fun fromSavedList(list: List<*>): VoterForm? {
            if (list.size != FIELD_COUNT || list.any { it !is String }) return null
            val s = list.map { it as String }
            return VoterForm(
                booth = s[0], boothSNo = s[1], ward = s[2], wardSNo = s[3],
                voterId = s[4], firstName = s[5], lastName = s[6], relativeName = s[7],
                houseNo = s[8], age = s[9], street = s[10], mobile = s[11], caste = s[12],
                village = s[13], panchayath = s[14], mandal = s[15], currentCity = s[16],
                isLocal = s[17], party = s[18], constituency = s[19],
            )
        }

        private const val FIELD_COUNT = 20

        fun from(e: VoterEntity): VoterForm = VoterForm(
            booth = e.booth.orEmpty(),
            boothSNo = e.boothSNo.orEmpty(),
            ward = e.ward.orEmpty(),
            wardSNo = e.wardSNo.orEmpty(),
            voterId = e.voterId,
            firstName = e.firstName,
            lastName = e.lastName.orEmpty(),
            relativeName = e.relativeName.orEmpty(),
            houseNo = e.houseNo.orEmpty(),
            age = e.age?.toString().orEmpty(),
            street = e.street.orEmpty(),
            mobile = e.mobile.orEmpty(),
            caste = e.caste.orEmpty(),
            village = e.village.orEmpty(),
            panchayath = e.panchayath.orEmpty(),
            mandal = e.mandal.orEmpty(),
            currentCity = e.currentCity.orEmpty(),
            isLocal = if (e.isLocal) Vocab.LOCAL else Vocab.NON_LOCAL,
            party = e.party.orEmpty(),
            constituency = e.constituency.orEmpty(),
        )
    }
}
