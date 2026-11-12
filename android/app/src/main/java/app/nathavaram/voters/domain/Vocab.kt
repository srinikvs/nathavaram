package app.nathavaram.voters.domain

/** Canonical dropdown values from docs/SCHEMA.md. */
object Vocab {
    val PARTIES: List<String> = listOf(
        "TDP", "YSRCP", "Jana Sena", "BJP", "INC", "CPI",
        "Independent", "Unknown", "Prefer not to say",
    )

    val GENDERS: List<String> = listOf("Male", "Female", "Other", "Unknown")

    const val DEFAULT_GENDER = "Unknown"

    const val YES = "Yes"
    const val NO = "No"

    /** "Local" answers. */
    val YES_NO: List<String> = listOf(NO, YES)

    /** Case-insensitive Yes/No; blank means No (the default). Null when it is neither. */
    fun canonicalLocal(raw: String): String? = when (raw.trim().lowercase()) {
        "", "no", "n", "false", "0" -> NO
        "yes", "y", "true", "1" -> YES
        else -> null
    }

    /** Case-insensitive match to the canonical party, or null when it is not a known value. */
    fun canonicalParty(raw: String): String? =
        PARTIES.firstOrNull { it.equals(raw.trim(), ignoreCase = true) }

    /** Case-insensitive match to the canonical gender, or null when it is not a known value. */
    fun canonicalGender(raw: String): String? =
        GENDERS.firstOrNull { it.equals(raw.trim(), ignoreCase = true) }
}
