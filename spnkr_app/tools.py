async def estimate_tier(self_counterfactuals, tier_counterfactuals):
    tiers = {
        "Bronze": 0,
        "Silver": 300,
        "Gold": 600,
        "Platinum": 900,
        "Diamond": 1200,
        "Onyx": 1500
    }

    kills = self_counterfactuals.kills
    deaths = self_counterfactuals.deaths

    # Convert tier names and values to lists for processing
    tier_names = list(tier_counterfactuals.keys())
    kill_values = [tier_counterfactuals[t].kills for t in tier_names]
    death_values = [tier_counterfactuals[t].deaths for t in tier_names]
    tier_values = [tiers[t] for t in tier_names]

    # Find the two closest tiers for interpolation
    for i in range(len(tier_names) - 1):
        lower_tier, upper_tier = tier_names[i], tier_names[i + 1]
        lower_kills, upper_kills = kill_values[i], kill_values[i + 1]
        lower_deaths, upper_deaths = death_values[i], death_values[i + 1]
        lower_value, upper_value = tier_values[i], tier_values[i + 1]

        if lower_kills <= kills <= upper_kills and lower_deaths >= deaths >= upper_deaths:
            # Interpolate for Kills
            kill_ratio = (kills - lower_kills) / (upper_kills - lower_kills)
            kill_tier = lower_value + (upper_value - lower_value) * kill_ratio

            # Interpolate for Deaths
            death_ratio = (deaths - lower_deaths) / (upper_deaths - lower_deaths)
            death_tier = lower_value + (upper_value - lower_value) * death_ratio

            # Average the two
            return round((kill_tier + death_tier) / 2)

    # Extrapolate for values beyond Onyx
    if kills > kill_values[-1] and deaths < death_values[-1]:
        kill_slope = (1500 - tier_values[-2]) / (kill_values[-1] - kill_values[-2])
        death_slope = (1500 - tier_values[-2]) / (death_values[-2] - death_values[-1])

        extrapolated_kill_tier = 1500 + (kills - kill_values[-1]) * kill_slope
        extrapolated_death_tier = 1500 + (deaths - death_values[-1]) * death_slope

        return round((extrapolated_kill_tier + extrapolated_death_tier) / 2)

    return None  # If no match is found