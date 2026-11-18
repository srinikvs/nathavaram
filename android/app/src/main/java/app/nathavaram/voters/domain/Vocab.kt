package app.nathavaram.voters.domain

/** Canonical dropdown values from docs/SCHEMA.md. */
object Vocab {
    val PARTIES: List<String> = listOf(
        "TDP", "YSRCP", "Jana Sena", "BJP", "INC", "CPI",
        "Independent", "Unknown", "Prefer not to say",
    )

    const val LOCAL = "Local"
    const val NON_LOCAL = "Non-Local"

    /** The Local/Non-Local answers, in the order they are offered. */
    val LOCAL_OPTIONS: List<String> = listOf(NON_LOCAL, LOCAL)

    /**
     * Case-insensitive Local / Non-Local (Yes/No, Y/N, true/false and 1/0 are accepted too);
     * blank means Non-Local (the default). Null when it is none of these.
     */
    fun canonicalLocal(raw: String): String? = when (raw.trim().lowercase()) {
        "", "non-local", "non local", "nonlocal", "no", "n", "false", "0" -> NON_LOCAL
        "local", "yes", "y", "true", "1" -> LOCAL
        else -> null
    }

    /** Case-insensitive match to the canonical party, or null when it is not a known value. */
    fun canonicalParty(raw: String): String? =
        PARTIES.firstOrNull { it.equals(raw.trim(), ignoreCase = true) }
}
