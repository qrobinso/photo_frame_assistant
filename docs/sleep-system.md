# Sleep System in Photo Frame Assistant

## Overview

The Photo Frame Assistant implements a sleep system that allows photo frames to conserve power while maintaining synchronized photo displays. This document explains how the sleep system works, including sleep intervals, deep sleep mode, synchronization groups, and the wake-sleep cycle workflow.

## Sleep Interval Basics

Each photo frame has a configurable `sleep_interval` property (default: 5.0 minutes) that determines how long the frame should sleep between wake cycles. During a wake cycle, the frame connects to the server, retrieves a new photo, and then returns to sleep to conserve power.

### Key Components:

- **Frame Sleep Interval**: The base time (in minutes) a frame should sleep between wake cycles
- **Last Wake Time**: Timestamp of when the frame last connected to the server
- **Next Wake Time**: Calculated timestamp of when the frame is expected to wake next

## Deep Sleep Mode

Deep sleep mode allows frames to enter an extended sleep period during specific hours (e.g., overnight), significantly extending battery life.

### How Deep Sleep Works:

1. Each frame can have deep sleep enabled/disabled independently
2. Deep sleep is configured with:
   - `deep_sleep_start`: Hour in 24-hour format (0-23) when deep sleep begins
   - `deep_sleep_end`: Hour in 24-hour format (0-23) when deep sleep ends

3. When a frame requests the next sleep interval during deep sleep hours, the server calculates the time until the end of deep sleep and returns that as the sleep interval

4. If the next regular wake time would fall within deep sleep hours, the server calculates the time until the end of deep sleep and returns that instead

## Synchronization Groups

Frames can be organized into synchronization groups to ensure they wake and change photos at the same time.

### How Sync Groups Work:

1. Each sync group has its own `sleep_interval` setting
2. The server calculates fixed sync points based on UTC time
3. When a frame in a sync group requests settings, the server:
   - Calculates the next sync point
   - Returns the time until that sync point as the sleep interval
   - If the time until next sync is too short (< 2 minutes), it skips to the following sync point

## Wake-Sleep Cycle Workflow

The wake-sleep cycle follows this workflow:

1. **Frame Wakes Up**:
   - Frame connects to server and requests settings via `/api/settings?device_id=<frame_id>`
   - Server records `last_wake_time` and calculates `next_wake_time`

2. **Server Calculates Sleep Interval**:
   - Checks if frame is in deep sleep mode
   - Checks if frame is in a sync group
   - Returns appropriate sleep interval based on these factors

3. **Frame Requests Photo**:
   - Frame requests next photo via `/api/next_photo?device_id=<frame_id>`
   - Server updates playlist order (moving current photo to end)
   - Server returns the photo with appropriate processing and overlays

4. **Frame Returns to Sleep**:
   - Frame sleeps for the duration specified in the sleep interval
   - Process repeats when frame wakes again

## Frame Status Determination

The server determines a frame's status based on its wake times and deep sleep settings:

- **Online**: Frame connected within the last 5 minutes
- **Sleeping**: Frame is between wake cycles but expected to wake soon
- **Deep Sleep**: Frame is in scheduled deep sleep period
- **Offline**: Frame missed its expected wake window by more than 10 minutes

## Sleep Interval Calculation Logic

The `calculate_sleep_interval()` function determines the appropriate sleep interval using this logic:

1. If deep sleep is not enabled, return the frame's configured sleep interval
2. If currently in deep sleep period, calculate minutes until deep sleep ends
3. If the next regular wake would be during deep sleep, calculate minutes until deep sleep ends
4. Otherwise, return the frame's configured sleep interval

## Timezone Handling

All sleep calculations are performed in UTC to ensure consistency, but the UI displays times in the server's configured timezone. The system handles timezone conversions when:

- Displaying wake times in the UI
- Converting between local time and UTC for deep sleep hours
- Calculating relative times for display

## Minimum Sleep Interval

The system enforces a minimum sleep interval (2 minutes) to prevent frames from waking too frequently, which could cause excessive battery drain or server load. 