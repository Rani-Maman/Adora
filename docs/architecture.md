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
    
    [*] --> DC: Daily Cron
    DC --> ST: Store Ads
    ST --> AN: Process Queue
    AN --> ST: Save Results
    API --> ST: Query
    CL --> API: Check URL
    API --> CL: Risk Score
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
        LoadKeywords --> CallFirecrawl
        CallFirecrawl --> ParseResponse
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
        [*] --> CheckPhysicalProduct
        CheckPhysicalProduct --> Skip: Not Physical
        CheckPhysicalProduct --> DetectRedFlags: Is Physical
        DetectRedFlags --> ScoreRisk
        ScoreRisk --> SearchAliExpress
        SearchAliExpress --> ComparePrice
        ComparePrice --> CalculateFinal
        CalculateFinal --> [*]
        Skip --> [*]
    }
    
    Analyzing --> Storing: Score Calculated
    
    state Storing {
        [*] --> SaveToPhysicalProducts
        SaveToPhysicalProducts --> CheckThreshold
        CheckThreshold --> AddToRiskDB: Score >= Threshold
        CheckThreshold --> Done: Score < Threshold
        AddToRiskDB --> Done
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
        QueryRiskDB --> ReturnRisk: Found
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
        ShowWarning --> [*]
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
    state "physical_products" as PP
    state "risk_db" as RISK
    
    [*] --> ADV: Scraper Inserts
    ADV --> ADS: Filter Valid URLs
    ADS --> PP: Analysis Complete
    PP --> RISK: Score >= Threshold
    RISK --> [*]: Extension Queries
```
