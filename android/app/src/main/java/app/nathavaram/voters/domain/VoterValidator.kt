package app.nathavaram.voters.domain

/** Client-side mirror of the rules in docs/SCHEMA.md. The server stays authoritative. */
object VoterValidator {
    sealed interface Result {
        /** [form] is trimmed and normalized: uppercase VoterId, canonical gender and party. */
        data class Ok(val form: VoterForm) : Result
        data class Invalid(val errors: Map<String, String>) : Result
    }

    const val AGE_MIN = 1
    const val AGE_MAX = 130
    private const val SHORT_MAX = 64
    private const val LONG_MAX = 128
    private val VOTER_ID = Regex("^[A-Z0-9]{3,32}$")
    private val MOBILE = Regex("^(?:\\+91|91|0)?[6-9]\\d{9}$")

    private val SHORT_FIELDS = listOf(
        FieldKey.BOOTH_2024, FieldKey.BS_NO_2024, FieldKey.WARD_2026,
        FieldKey.WARD_S_NO, FieldKey.HOUSE_NO,
    )
    private val LONG_FIELDS = listOf(
        FieldKey.SURNAME, FieldKey.RELATIVE_NAME, FieldKey.CASTE,
        FieldKey.C_CITY, FieldKey.PROFESSION,
    )

    fun validate(input: VoterForm): Result {
        val errors = linkedMapOf<String, String>()
        var form = input
        for (key in ALL_KEYS) form = form.with(key, input.get(key).trim())

        for (key in ALL_KEYS) {
            if (form.get(key).any(::isControl)) errors[key] = "Contains an invalid character"
        }
        for (key in SHORT_FIELDS) checkLength(form, key, SHORT_MAX, errors)
        for (key in LONG_FIELDS) checkLength(form, key, LONG_MAX, errors)

        form = form.copy(voterId = form.voterId.uppercase())
        if (FieldKey.VOTER_ID !in errors && !VOTER_ID.matches(form.voterId)) {
            errors[FieldKey.VOTER_ID] = "3 to 32 letters or digits"
        }

        if (form.firstName.isEmpty()) {
            errors[FieldKey.FIRST_NAME] = "Required"
        } else {
            checkLength(form, FieldKey.FIRST_NAME, LONG_MAX, errors)
        }

        if (form.gender.isEmpty()) {
            form = form.copy(gender = Vocab.DEFAULT_GENDER)
        } else {
            val gender = Vocab.canonicalGender(form.gender)
            if (gender == null) errors[FieldKey.GENDER] = "Choose Male, Female, Other or Unknown"
            else form = form.copy(gender = gender)
        }

        if (form.age.isNotEmpty()) {
            val age = form.age.toIntOrNull()
            if (age == null || age !in AGE_MIN..AGE_MAX) {
                errors[FieldKey.AGE] = "Whole number from $AGE_MIN to $AGE_MAX"
            }
        }

        if (form.mobile.isNotEmpty() && !MOBILE.matches(form.mobile.replace(" ", "").replace("-", ""))) {
            errors[FieldKey.MOBILE] = "Enter a 10-digit Indian mobile number"
        }

        if (form.party.isNotEmpty()) {
            val party = Vocab.canonicalParty(form.party)
            if (party == null) errors[FieldKey.PARTY] = "Choose a party from the list"
            else form = form.copy(party = party)
        }

        return if (errors.isEmpty()) Result.Ok(form) else Result.Invalid(errors)
    }

    private fun checkLength(form: VoterForm, key: String, max: Int, errors: MutableMap<String, String>) {
        if (key !in errors && form.get(key).length > max) errors[key] = "At most $max characters"
    }

    private fun isControl(c: Char): Boolean = c.code < 32 || c.code == 127

    private val ALL_KEYS = listOf(
        FieldKey.BOOTH_2024, FieldKey.BS_NO_2024, FieldKey.WARD_2026, FieldKey.WARD_S_NO,
        FieldKey.VOTER_ID, FieldKey.FIRST_NAME, FieldKey.SURNAME, FieldKey.RELATIVE_NAME,
        FieldKey.HOUSE_NO, FieldKey.GENDER, FieldKey.AGE, FieldKey.CASTE, FieldKey.MOBILE,
        FieldKey.C_CITY, FieldKey.PROFESSION, FieldKey.PARTY,
    )
}
