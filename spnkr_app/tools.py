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

    tier_names = list(tier_counterfactuals.keys())
    kill_values = [tier_counterfactuals[t].kills for t in tier_names]
    death_values = [tier_counterfactuals[t].deaths for t in tier_names]
    tier_values = [tiers[t] for t in tier_names]
    
    # Ensure kills and deaths are within the tiers' range
    if kills <= kill_values[0]:
        return tiers[tier_names[0]]
    if kills >= kill_values[-1]:
        extrapolated_tier = tier_values[-1] + (kills - kill_values[-1]) * (tier_values[-1] - tier_values[-2]) / (kill_values[-1] - kill_values[-2])
        return round(extrapolated_tier)
    
    # Interpolation between tiers
    for i in range(len(tier_names) - 1):
        lower_kills, upper_kills = kill_values[i], kill_values[i + 1]
        lower_deaths, upper_deaths = death_values[i], death_values[i + 1]
        lower_value, upper_value = tier_values[i], tier_values[i + 1]
        
        if lower_kills <= kills <= upper_kills:
            kill_ratio = (kills - lower_kills) / (upper_kills - lower_kills)
            kill_tier = lower_value + (upper_value - lower_value) * kill_ratio
            
            death_ratio = (deaths - lower_deaths) / (upper_deaths - lower_deaths)
            death_tier = lower_value + (upper_value - lower_value) * death_ratio
            
            return round((kill_tier + death_tier) / 2)
    
    return round(tier_values[-1])
    