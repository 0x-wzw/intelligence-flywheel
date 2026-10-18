#!/usr/bin/env python3
"""Refined scan: only genuine generic ChatGPT/prompt/marketing templates."""
import os

NOTION_DIR = "/Users/admin/Documents/Obsidian Vault/Inbox/Notion"
os.chdir(NOTION_DIR)

# These are the actual generic prompt/marketing templates (not business docs)
# Manually curated from the 210 candidates
templates = [
    # ChatGPT Tips & Prompt Engineering (8)
    "4-powerful-chatgpt-prompts-for-entrepreneurs.md",
    "becoming-an-expert-in-chatgpt.md",
    "chatgpt-advanced-secret-tool-to-write-faster-and-better-content.md",
    "chatgpt-ecommerce-hacks.md",
    "find-your-voice-with-chatgpt.md",
    "general-chatgpt-tips.md",
    "how-chatgpt-actually-works.md",
    "how-to-turn-chatgpt-into-literally-anyone.md",
    "top-5-chatgpt-extensions.md",
    "snippets-to-improve-chatgpt-writing-style.md",
    "chatgpt-to-transform-your-tiktok-career.md",
    "prompting.md",
    "prompting-tomorrows-consumer-products-with-ai.md",
    "stay-ahead-in-2023-with-chatgpt.md",
    
    # SEO & Search (8)
    "25-templates-for-seo-optimization.md",
    "50-smart-seo-prompts-for-chatgpt.md",
    "automating-keyword-research.md",
    "conducting-backlink-research.md",
    "conducting-keyword-research-for-lead-generation.md",
    "conducting-keyword-research.md",
    "creating-sitemaps.md",
    "providing-keyword-research.md",
    "generating-backend-keywords.md",
    "generating-google-ads-keywords.md",
    "generating-long-tail-keywords.md",
    "generating-longtail-keywords.md",
    "monitoring-competitor-keywords.md",
    
    # Email & SMS Marketing (19)
    "25-chatgpt-prompts-to-25x-your-email-copywriting-results.md",
    "5-prompts-you-can-steal-to-10x-your-email-copywriting-results.md",
    "Generating-Cold-Email-Copy's.md",
    "Generating-Email-Followup-Sequences.md",
    "building-email-automations.md",
    "developing-effective-email-campaigns-for-lead-nurturing.md",
    "dynamic-email-marketing.md",
    "generating-abandoned-cart-recovery-emails.md",
    "generating-cold-email-copys.md",
    "generating-email-campaign-ideas.md",
    "generating-email-follow-up-sequences.md",
    "generating-email-lead-magnet-ideas.md",
    "generating-ideas-for-email-newsletters.md",
    "responding-to-customer-emails.md",
    "writing-email-newsletters.md",
    "writing-email-subject-lines.md",
    "writing-product-launch-emails.md",
    "writing-sales-emails.md",
    "sms-marketing.md",
    
    # Social Media Content (23)
    "automating-instagram-growth.md",
    "automating-linkedin-content-growth.md",
    "automating-tiktok-channel-growth.md",
    "automating-twitter-growth.md",
    "b2b-linkedin-growth-automation.md",
    "b2b-twitter-growth-automation.md",
    "building-linkedin-hashtags.md",
    "coming-up-with-dozens-of-cool-captions.md",
    "creating-tiktok-content-ideas.md",
    "generating-creative-ideas-for-tiktok-trends.md",
    "generating-facebook-ads-targeting-ideas.md",
    "generating-instagram-ads-targeting-ideas.md",
    "generating-linkedin-ads-targeting-ideas.md",
    "generating-linkedin-content-ideas.md",
    "generating-pinterest-ads-targeting-ideas.md",
    "generating-pinterest-board-ideas.md",
    "generating-twitter-ads-targeting-ideas.md",
    "going-viral-on-twitter.md",
    "inbounding-lead-generation-on-twitter.md",
    "twitter-profile-optimization.md",
    "writing-linkedin-posts.md",
    "writing-twitter-content.md",
    "writing-twitter-threads.md",
    "writing-viral-tiktok-scripts.md",
    
    # Blog & Articles (5)
    "developing-content-for-company-blogs.md",
    "generating-blog-post-outlines.md",
    "generating-blog-post-titles.md",
    "writing-blog-posts.md",
    "writing-guest-posts.md",
    "writing-guest-post-pitches.md",
    "crafting-guest-blog-post-pitches.md",
    
    # Video & Scripts (9)
    "automating-growth-on-youtube.md",
    "automating-youtube-channel-growth.md",
    "creating-content-with-chatgpt-the-scripts-experiments.md",
    "creating-meta-descriptions.md",
    "creating-video-content.md",
    "creating-video-from-text.md",
    "creating-youtube-video-scripts.md",
    "writing-a-brief-for-video-editor.md",
    "writing-video-descriptions.md",
    
    # Sales Copy & Pitches (13)
    "Writing-BenefitDriven-Sales-Copy.md",
    "Writing-LongForm-Sales-Copy.md",
    "Writing-MediumForm-Sales-Copy.md",
    "writing-benefit-driven-sales-copy.md",
    "writing-long-form-sales-copy.md",
    "writing-medium-form-sales-copy.md",
    "writing-ad-copy-that-converts.md",
    "writing-sales-copy-for-cold-calling.md",
    "writing-a-proposal-for-a-potential-client.md",
    "writing-pitch-for-influencer.md",
    "writing-product-descriptions.md",
    "writing-100-product-descriptions-in-seconds.md",
    "writing-product-reviews.md",
    "writing-product-testimonials.md",
    "enhancing-product-descriptions.md",
    "optimizing-product-titles.md",
    "improving-product-images.md",
    "improving-product-ratings.md",
    "increasing-product-reviews.md",
    "creating-product-comparison-charts.md",
    "creating-product-bundling-strategies.md",
    "creating-product-placement-opportunities.md",
    "creating-product-advice-content.md",
    "creating-effective-bullet-points.md",
    "product-launching.md",
    
    # Advertising & Ad Copy (4)
    "generating-ad-copy-ideas.md",
    "making-ad-copy-more-interesting.md",
    "translating-ad-copy.md",
    "rewriting-ab-version-of-winning-creative.md",
    "setting-up-amazon-ads.md",
    "creating-amazon-brand-store.md",
    
    # Lead Generation (9)
    "developing-lead-capture-forms.md",
    "generating-lead-magnet-ideas.md",
    "creating-engaging-lead-magnets.md",
    "conducting-webinars-for-lead-generation.md",
    "conducting-surveys-for-lead-generation.md",
    "developing-social-media-strategies-for-lead-gen.md",
    "analyzing-lead-generation-metrics.md",
    "conducting-competitor-research-for-lead-capture.md",
    "conducting-keyword-research-for-lead-generation.md",
    
    # PR & Media Relations (9)
    "drafting-press-releases-for-maximum-impact.md",
    "creating-brand-messaging-for-pr.md",
    "creating-social-media-strategies-for-pr.md",
    "creating-compelling-story-angles.md",
    "identifying-media-outlets-for-outreach.md",
    "conducting-media-interviews-and-features.md",
    "hosting-media-events-and-press-conferences.md",
    "providing-crisis-management-guidance.md",
    "analyzing-pr-engagement-metrics.md",
    
    # Influencer & Affiliate (11)
    "automating-influencer-outreach.md",
    "developing-influencer-partnership-strategies.md",
    "finding-new-affiliates.md",
    "Identifying-HighPerforming-Affiliates.md",
    "identifying-high-performing-affiliates.md",
    "generating-influencer-agreements.md",
    "hosting-webinars-for-affiliates.md",
    "managing-affiliate-relationships.md",
    "personalizing-messages-for-influencers.md",
    "writing-creative-briefs-for-influencer.md",
    "influencer-marketing-dd.md",
    
    # Conversion & Offers (10)
    "Optimizing-CTA's.md",
    "optimizing-ctas.md",
    "crafting-irresistible-offers.md",
    "crafting-unique-usps.md",
    "developing-new-offers.md",
    "optimizing-for-conversions.md",
    "creating-incentive-programs.md",
    "creating-promo-strategies.md",
    "generating-discount-ideas.md",
    "offer-letter-automation.md",
    
    # Customer Lifecycle (4)
    "developing-customer-retention-strategies.md",
    "developing-referral-marketing-strategies.md",
    "launching-referral-contests.md",
    "onboarding-messages.md",
    "creating-faqs-for-customers.md",
    
    # Research & Analytics (13)
    "conducting-competitor-research.md",
    "conducting-competitor-analysis.md",
    "researching-competitors.md",
    "analyzing-performance-metrics.md",
    "analyzing-search-term-reports.md",
    "analyzing-url-links.md",
    "providing-performance-reports.md",
    "writing-performance-reports.md",
    "generating-product-feedback-surveys.md",
    "generating-ideas-for-customer-surveys.md",
    "summarizing-customer-feedback.md",
    "summarizing-meeting-notes.md",
    "identifying-trending-topics.md",
    
    # Visual & Design (9)
    "create-iconography-for-feature.md",
    "create-visuals-for-feature.md",
    "create-wireframes-of-feature.md",
    "creating-custom-visuals-for-posts.md",
    "creating-infographics.md",
    "creating-visual-ideas-for-website.md",
    "design-icons-and-buttons.md",
    "finalize-visuals-for-feature.md",
    "generating-product-comparison-infographics.md",
    
    # Chatbots & Conversational AI (13)
    "Building-Chatbots-for-Ecommerce.md",
    "building-chatbots-for-e-commerce.md",
    "chatbot-optimization-for-lead-generation.md",
    "creating-chatbots-for-customer-service.md",
    "creating-chatbots-for-social-media.md",
    "designing-chatbot-user-experience.md",
    "developing-chatbot-conversation-flows.md",
    "developing-multilingual-chatbots.md",
    "implementing-chatbots-for-website-support.md",
    "implementing-chatbots-on-various-channels.md",
    "analyzing-chatbot-engagement-and-conversion-metrics.md",
    "chatbot-security-and-data-privacy.md",
    "customizing-chatbots-to-brand-voice-and-tone.md",
    
    # General Marketing (8)
    "brainstorming-unique-marketing-angles.md",
    "create-marketing-materials.md",
    "create-organic-content.md",
    "digital-marketing.md",
    "getting-ideas-for-headlines.md",
    "marketing-2099-ultimate-chatgpt-marketing-prompts-to-copy-paste-200-tasks.md",
    "translating-website-copy.md",
    "developing-a-brand-voice-and-tone-guide.md",
    
    # Misc (5)
    "24-copywriting-advanced-prompts.md",
    "The-Variety-of-\"Act-as\"-Personas.md",
    "625-post-ideas-in-less-than-5-min.md",
    "negotiating-commission-rates.md",
    "networking-and-relationship-building.md",
]

# Verify which exist
existing = [f for f in templates if os.path.isfile(f)]
missing = [f for f in templates if not os.path.isfile(f)]

print(f"Total identified: {len(templates)}")
print(f"Existing: {len(existing)}")
print(f"Missing: {len(missing)}")
if missing:
    print(f"\nMissing files:")
    for f in missing:
        print(f"  • {f}")
PYEOF
