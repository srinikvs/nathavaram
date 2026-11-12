package app.nathavaram.voters.domain

/** Canonical dropdown values from docs/SCHEMA.md. */
object Vocab {
    val PARTIES: List<String> = listOf(
        "TDP", "YSRCP", "Jana Sena", "BJP", "INC", "CPI",
        "Independent", "Unknown", "Prefer not to say",
    )

    val GENDERS: List<String> = listOf("Male", "Female", "Other", "Unknown")

    const val DEFAULT_GENDER = "Unknown"

    /** Case-insensitive match to the canonical party, or null when it is not a known value. */
    fun canonicalParty(raw: String): String? =
        PARTIES.firstOrNull { it.equals(raw.trim(), ignoreCase = true) }

    /** Case-insensitive match to the canonical gender, or null when it is not a known value. */
    fun canonicalGender(raw: String): String? =
        GENDERS.firstOrNull { it.equals(raw.trim(), ignoreCase = true) }
}
