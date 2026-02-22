# Adora - Component State Machine Diagrams

## System Overview

```mermaid
stateDiagram-v2
    direction LR
    
    state "Data Collection" as DC
    state "Storage" as ST
    state "Analysis" as AN
    state "API" as API
    state "Client" as CL
    
    state "Price Match" as PM

    [*] --> DC: Daily Cron
    DC --> ST: Store Ads
    ST --> AN: Process Queue
    AN --> ST: Save Results
    ST --> PM: Risky Domains
    PM --> ST: Save Matches
    API --> ST: Query
    CL --> API: Check URL
    API --> CL: Risk + Prices
```

---

## Component 1: Data Collection Pipeline

```mermaid
stateDiagram-v2
    direction TB
    
    [*] --> Idle
    
    Idle --> Scraping: Cron Trigger (00:01)
    
    state Scraping {
        [*] --> LoadKeywords
        LoadKeywords --> QueryGraphQL
        QueryGraphQL --> ParseResponse
        ParseResponse --> ExtractAds
        ExtractAds --> [*]
    }
    
    Scraping --> Storing: Ads Extracted
    
    state Storing {
        [*] --> CheckDuplicate
        CheckDuplicate --> InsertAdvertiser: New
        CheckDuplicate --> Skip: Duplicate
        InsertAdvertiser --> FilterURL
        FilterURL --> InsertAdsWithUrls: Valid URL
        FilterURL --> Skip: fb.me/instagram/whatsapp
        InsertAdsWithUrls --> [*]
        Skip --> [*]
    }
    
    Storing --> SendReport: All Keywords Done
    SendReport --> Idle: Email Sent
```

---

## Component 2: Analysis Engine

```mermaid
stateDiagram-v2
    direction TB
    
    [*] --> Pending
    
    Pending --> Fetching: Process Trigger
    
    state Fetching {
        [*] --> GetUnanalyzed
        GetUnanalyzed --> ScrapeProductPage
        ScrapeProductPage --> ExtractContent
        ExtractContent --> [*]
    }
    
    Fetching --> Analyzing: Content Ready
    
    state Analyzing {
        [*] --> SendToGemini: Gemini 2.5 Flash + Grounding
        SendToGemini --> ParseResult
        ParseResult --> NormalizeCategory
        NormalizeCategory --> [*]
    }
    
    Analyzing --> Storing: Score Calculated
    
    state Storing {
        [*] --> UpdateAdsWithUrls
        UpdateAdsWithUrls --> CheckThreshold
        CheckThreshold --> AddToRiskDB: Score >= Threshold
        CheckThreshold --> RemoveFromRiskDB: Score < Threshold (re-analysis)
        CheckThreshold --> Done: Score < Threshold (new)
        AddToRiskDB --> Done
        RemoveFromRiskDB --> Done
        Done --> [*]
    }
    
    Storing --> Pending: Process Next
```

---

## Component 3: REST API

```mermaid
stateDiagram-v2
    direction TB
    
    [*] --> Ready
    
    Ready --> ProcessRequest: GET /check?url=X
    
    state ProcessRequest {
        [*] --> NormalizeURL
        NormalizeURL --> CheckWhitelist
        CheckWhitelist --> ReturnSafe: In Whitelist
        CheckWhitelist --> CheckTrustedTLD: Not in Whitelist
        CheckTrustedTLD --> ReturnSafe: .gov.il/.ac.il/.edu
        CheckTrustedTLD --> QueryRiskDB: Unknown
        QueryRiskDB --> ReturnRisk: Found (score + price_matches)
        QueryRiskDB --> ReturnUnknown: Not Found
        ReturnSafe --> [*]
        ReturnRisk --> [*]
        ReturnUnknown --> [*]
    }
    
    ProcessRequest --> Ready: Response Sent
```

---

## Component 4: Chrome Extension

```mermaid
stateDiagram-v2
    direction TB
    
    [*] --> Installed
    
    Installed --> FetchWhitelist: On Install
    FetchWhitelist --> Idle: Cached
    
    Idle --> UserNavigates: URL Change
    
    state UserNavigates {
        [*] --> ExtractBaseURL
        ExtractBaseURL --> CheckLocalCache
        CheckLocalCache --> ShowCached: Cache Hit
        CheckLocalCache --> CheckWhitelist: Cache Miss
        CheckWhitelist --> SkipCheck: Whitelisted
        CheckWhitelist --> CallAPI: Not Whitelisted
        CallAPI --> CacheResponse
        CacheResponse --> ShowWarning: Is Risky
        CacheResponse --> Silent: Not Risky
        ShowCached --> ShowWarning: Was Risky
        ShowCached --> Silent: Was Safe
        SkipCheck --> Silent
        ShowWarning --> ShowPrices: Has Matches
        ShowWarning --> ShowScanning: No Matches Yet
        ShowPrices --> [*]
        ShowScanning --> [*]
        Silent --> [*]
    }
    
    UserNavigates --> Idle: Done
    
    Idle --> RefreshWhitelist: Weekly Timer
    RefreshWhitelist --> Idle: Updated
```

---

## Database State Flow

```mermaid
stateDiagram-v2
    direction LR
    
    state "advertisers" as ADV
    state "ads_with_urls" as ADS
    state "risk_db" as RISK

    [*] --> ADV: Scraper Inserts
    ADV --> ADS: Filter Valid URLs
    ADS --> RISK: Score >= Threshold
    RISK --> [*]: Extension Queries
```
