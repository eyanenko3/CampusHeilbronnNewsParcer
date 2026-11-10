/**
 * Sliding-Down React Native Screen for the TUM Campus Heilbronn News & Events Feed.
 *
 * Features:
 *   - Source tabs: All | 📰 TUM News (`tum_chn_news`) | 📅 Events (`tum_chn_events`)
 *   - Slide-down filter & search drawer (`LayoutAnimation` transition)
 *   - Slide-down expandable cards: tapping a card smoothly slides open the full
 *     scraped article/event description, metadata, and Original Source / Register links
 */

import React, { useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  LayoutAnimation,
  Linking,
  Platform,
  Pressable,
  RefreshControl,
  SafeAreaView,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  UIManager,
  View,
} from "react-native";
import { CampusEvent, ChnEventsApiClient, useCampusEvents } from "./chnApiClient";

if (Platform.OS === "android" && UIManager.setLayoutAnimationEnabledExperimental) {
  UIManager.setLayoutAnimationEnabledExperimental(true);
}

export interface EventsFeedScreenProps {
  /** Base URL of `database/api_server.py` or Firebase Hosting site. */
  apiBaseUrl?: string;
}

function formatEventDateTime(event: CampusEvent): string {
  const start = new Date(event.starts_at);
  const datePart = start.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
  if (event.source_key === "tum_chn_news") {
    return `Published ${datePart}`;
  }
  if (event.all_day) {
    return `${datePart} (All day)`;
  }
  const timePart = start.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (!event.ends_at) {
    return `${datePart} • ${timePart}`;
  }
  const end = new Date(event.ends_at);
  const sameDay = start.toDateString() === end.toDateString();
  const endTimePart = end.toLocaleTimeString("en-GB", {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (sameDay) {
    return `${datePart} • ${timePart}–${endTimePart}`;
  }
  const endDatePart = end.toLocaleDateString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
  return `${datePart} – ${endDatePart} • ${timePart}–${endTimePart}`;
}

export const EventsFeedScreen: React.FC<EventsFeedScreenProps> = ({
  apiBaseUrl = "http://localhost:8080",
}) => {
  const client = useMemo(() => new ChnEventsApiClient(apiBaseUrl), [apiBaseUrl]);
  const { events, total, tags, filters, setFilters, loading, error, refresh } =
    useCampusEvents(client, { from: "all" });

  const [drawerOpen, setDrawerOpen] = useState<boolean>(true);
  const [expandedIds, setExpandedIds] = useState<Record<number, boolean>>({});

  const activeSource = filters.sources?.[0] ?? "all";
  const activeTag = filters.tags?.[0] ?? null;

  const toggleDrawer = () => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setDrawerOpen((prev) => !prev);
  };

  const toggleCard = (id: number) => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setExpandedIds((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const selectSource = (sourceKey: "all" | "tum_chn_news" | "tum_chn_events") => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setFilters((prev) => ({
      ...prev,
      sources: sourceKey === "all" ? undefined : [sourceKey],
      offset: 0,
    }));
  };

  const selectTag = (slug: string | null) => {
    LayoutAnimation.configureNext(LayoutAnimation.Presets.easeInEaseOut);
    setFilters((prev) => ({
      ...prev,
      tags: slug ? [slug] : undefined,
      offset: 0,
    }));
  };

  return (
    <SafeAreaView style={styles.safe}>
      {/* Sticky Top Bar */}
      <View style={styles.header}>
        <View style={styles.headerRow}>
          <View>
            <Text style={styles.headerTitle}>TUM Campus Heilbronn</Text>
            <Text style={styles.headerSubtitle}>Slide-Down News & Events Hub</Text>
          </View>
          <Pressable style={styles.drawerToggle} onPress={toggleDrawer}>
            <Text style={styles.drawerToggleText}>
              Filters {drawerOpen ? "▴" : "▾"}
            </Text>
          </Pressable>
        </View>

        {/* Source Segmented Tabs */}
        <View style={styles.sourceTabs}>
          {(
            [
              { key: "all", label: "All" },
              { key: "tum_chn_news", label: "📰 TUM News" },
              { key: "tum_chn_events", label: "📅 Events" },
            ] as const
          ).map((tab) => {
            const isActive = activeSource === tab.key;
            return (
              <Pressable
                key={tab.key}
                onPress={() => selectSource(tab.key)}
                style={[styles.sourceTab, isActive && styles.sourceTabActive]}
              >
                <Text
                  style={[
                    styles.sourceTabText,
                    isActive && styles.sourceTabTextActive,
                  ]}
                >
                  {tab.label}
                </Text>
              </Pressable>
            );
          })}
        </View>
      </View>

      {/* Slide-Down Search & Filter Drawer */}
      {drawerOpen ? (
        <View style={styles.slideDrawer}>
          <TextInput
            style={styles.searchInput}
            placeholder="Search TUM news & events (e.g. Edge AI, Career)..."
            placeholderTextColor="#6c7a89"
            value={filters.q ?? ""}
            onChangeText={(text) =>
              setFilters((prev) => ({ ...prev, q: text, offset: 0 }))
            }
          />

          <ScrollView
            horizontal
            showsHorizontalScrollIndicator={false}
            contentContainerStyle={styles.chipsRow}
          >
            <Pressable
              onPress={() => selectTag(null)}
              style={[styles.chip, activeTag === null && styles.chipActive]}
            >
              <Text
                style={[styles.chipText, activeTag === null && styles.chipTextActive]}
              >
                All Topics ({total})
              </Text>
            </Pressable>

            {tags.map((tag) => {
              const isActive = activeTag === tag.slug;
              return (
                <Pressable
                  key={tag.slug}
                  onPress={() => selectTag(isActive ? null : tag.slug)}
                  style={[styles.chip, isActive && styles.chipActive]}
                >
                  <Text style={[styles.chipText, isActive && styles.chipTextActive]}>
                    {tag.label} ({tag.upcoming_count})
                  </Text>
                </Pressable>
              );
            })}
          </ScrollView>
        </View>
      ) : null}

      {/* Main Feed */}
      {error ? (
        <View style={styles.centerBox}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.primaryBtn} onPress={() => void refresh()}>
            <Text style={styles.primaryBtnText}>Retry</Text>
          </Pressable>
        </View>
      ) : loading && events.length === 0 ? (
        <View style={styles.centerBox}>
          <ActivityIndicator size="large" color="#0065bd" />
        </View>
      ) : (
        <FlatList
          data={events}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.listContent}
          refreshControl={
            <RefreshControl refreshing={loading} onRefresh={() => void refresh()} />
          }
          renderItem={({ item }) => {
            const isNews = item.source_key === "tum_chn_news";
            const isExpanded = Boolean(expandedIds[item.id]);

            return (
              <View style={styles.card}>
                <Pressable onPress={() => toggleCard(item.id)}>
                  {item.image_url ? (
                    <View>
                      <Image
                        source={{ uri: item.image_url }}
                        style={styles.cardImage}
                      />
                      <View
                        style={[
                          styles.badgePill,
                          isNews ? styles.badgeNews : styles.badgeEvent,
                        ]}
                      >
                        <Text style={styles.badgePillText}>
                          {isNews ? "📰 TUM NEWS" : "📅 EVENT"}
                        </Text>
                      </View>
                    </View>
                  ) : null}

                  <View style={styles.cardBody}>
                    <Text style={styles.cardMeta}>{formatEventDateTime(item)}</Text>
                    {item.location ? (
                      <Text style={styles.cardLocation} numberOfLines={1}>
                        📍 {item.location}
                      </Text>
                    ) : null}
                    <Text style={styles.cardTitle}>{item.title}</Text>
                    {item.summary || item.description ? (
                      <Text
                        style={styles.cardSummary}
                        numberOfLines={isExpanded ? undefined : 3}
                      >
                        {item.summary ?? item.description}
                      </Text>
                    ) : null}

                    <View style={styles.slideHintRow}>
                      <View style={styles.tagsWrap}>
                        {item.tags.map((t) => (
                          <View key={t.slug} style={styles.tagBadge}>
                            <Text style={styles.tagBadgeText}>{t.label}</Text>
                          </View>
                        ))}
                      </View>
                      <Text style={styles.slideHintText}>
                        {isExpanded ? "Slide up ▴" : "Slide down ▾"}
                      </Text>
                    </View>
                  </View>
                </Pressable>

                {/* Slide-Down Expanded Drawer inside Card */}
                {isExpanded ? (
                  <View style={styles.expandedDrawer}>
                    {item.description && item.description !== item.summary ? (
                      <Text style={styles.fullDescription}>{item.description}</Text>
                    ) : null}
                    {item.organizer ? (
                      <Text style={styles.cardLocation}>🏛️ {item.organizer}</Text>
                    ) : null}
                    <View style={styles.actionsRow}>
                      <Pressable
                        style={styles.primaryBtn}
                        onPress={() => void Linking.openURL(item.url)}
                      >
                        <Text style={styles.primaryBtnText}>
                          {isNews ? "Read Full Article ↗" : "Original Event Page ↗"}
                        </Text>
                      </Pressable>
                      {item.registration_url ? (
                        <Pressable
                          style={styles.secondaryBtn}
                          onPress={() =>
                            void Linking.openURL(item.registration_url!)
                          }
                        >
                          <Text style={styles.secondaryBtnText}>Register ↗</Text>
                        </Pressable>
                      ) : null}
                    </View>
                  </View>
                ) : null}
              </View>
            );
          }}
        />
      )}
    </SafeAreaView>
  );
};

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: "#f3f6f9" },
  header: { backgroundColor: "#072140", paddingHorizontal: 16, paddingVertical: 12 },
  headerRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginBottom: 10,
  },
  headerTitle: { color: "#ffffff", fontSize: 18, fontWeight: "700" },
  headerSubtitle: { color: "#b4c6db", fontSize: 12, marginTop: 2 },
  drawerToggle: {
    borderColor: "rgba(255,255,255,0.28)",
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
  },
  drawerToggleText: { color: "#ffffff", fontSize: 12, fontWeight: "600" },
  sourceTabs: {
    flexDirection: "row",
    backgroundColor: "rgba(255,255,255,0.1)",
    borderRadius: 999,
    padding: 3,
  },
  sourceTab: {
    flex: 1,
    paddingVertical: 6,
    borderRadius: 999,
    alignItems: "center",
  },
  sourceTabActive: { backgroundColor: "#0065bd" },
  sourceTabText: { color: "#d0deee", fontSize: 13, fontWeight: "600" },
  sourceTabTextActive: { color: "#ffffff" },
  slideDrawer: {
    backgroundColor: "#0b2b52",
    paddingHorizontal: 16,
    paddingTop: 12,
    paddingBottom: 6,
  },
  searchInput: {
    backgroundColor: "#ffffff",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 9,
    fontSize: 14,
    color: "#072140",
  },
  chipsRow: { paddingVertical: 10, gap: 8 },
  chip: {
    backgroundColor: "rgba(255,255,255,0.1)",
    borderColor: "rgba(255,255,255,0.22)",
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 13,
    paddingVertical: 6,
    marginRight: 8,
  },
  chipActive: { backgroundColor: "#ffffff", borderColor: "#ffffff" },
  chipText: { color: "#e4eef8", fontSize: 12, fontWeight: "600" },
  chipTextActive: { color: "#072140" },
  listContent: { padding: 14 },
  card: {
    backgroundColor: "#ffffff",
    borderColor: "#dde4ec",
    borderWidth: 1,
    borderRadius: 14,
    overflow: "hidden",
    marginBottom: 14,
  },
  cardImage: { width: "100%", height: 175, backgroundColor: "#e6ecf2" },
  badgePill: {
    position: "absolute",
    top: 10,
    left: 10,
    paddingHorizontal: 10,
    paddingVertical: 4,
    borderRadius: 999,
  },
  badgeNews: { backgroundColor: "#e37222" },
  badgeEvent: { backgroundColor: "#0065bd" },
  badgePillText: { color: "#ffffff", fontSize: 10, fontWeight: "700" },
  cardBody: { padding: 14 },
  cardMeta: { color: "#0065bd", fontSize: 12, fontWeight: "600", marginBottom: 4 },
  cardLocation: { color: "#5b6b7c", fontSize: 12, marginBottom: 6 },
  cardTitle: { color: "#072140", fontSize: 17, fontWeight: "700", marginBottom: 6 },
  cardSummary: { color: "#2c3e50", fontSize: 14, lineHeight: 20, marginBottom: 10 },
  slideHintRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    marginTop: 4,
  },
  tagsWrap: { flexDirection: "row", flexWrap: "wrap", gap: 6, flex: 1 },
  tagBadge: {
    backgroundColor: "#edf3f9",
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: 5,
  },
  tagBadgeText: { color: "#1c3d63", fontSize: 11, fontWeight: "600" },
  slideHintText: { color: "#0065bd", fontSize: 12, fontWeight: "700", marginLeft: 8 },
  expandedDrawer: {
    backgroundColor: "#f9fbfd",
    borderTopColor: "#dde4ec",
    borderTopWidth: 1,
    padding: 14,
  },
  fullDescription: { color: "#1e2f42", fontSize: 14, lineHeight: 21, marginBottom: 10 },
  actionsRow: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 6 },
  primaryBtn: {
    backgroundColor: "#0065bd",
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 7,
  },
  primaryBtnText: { color: "#ffffff", fontSize: 13, fontWeight: "600" },
  secondaryBtn: {
    borderColor: "#0065bd",
    borderWidth: 1,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 7,
  },
  secondaryBtnText: { color: "#0065bd", fontSize: 13, fontWeight: "600" },
  centerBox: { flex: 1, justifyContent: "center", alignItems: "center", padding: 24 },
  errorText: { color: "#c0392b", fontSize: 14, marginBottom: 12, textAlign: "center" },
});
