import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "@/components/Navbar";
import PricingCard from "@/components/PricingCard";
import { useAuth } from "@/contexts/AuthContext";
import { client } from "@/lib/api";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import { Button } from "@/components/ui/button";
import {
  ArrowRight,
  Bot,
  Code2,
  Eye,
  Rocket,
  Sparkles,
  Zap,
  Users,
  Shield,
  Globe,
  Loader2,
} from "lucide-react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

const HERO_BG = "https://mgx-backend-cdn.metadl.com/generate/images/1069105/2026-03-29/a58369f6-e59a-454f-986e-d7347e612693.png";
const FEATURE_AI = "https://mgx-backend-cdn.metadl.com/generate/images/1069105/2026-03-29/27cb7eea-2e57-4edb-9eb8-9c437626fcb3.png";
const FEATURE_CODE = "https://mgx-backend-cdn.metadl.com/generate/images/1069105/2026-03-29/548ce3e0-3d48-4dff-ab25-c8d80e17473b.png";
const FEATURE_DEPLOY = "https://mgx-backend-cdn.metadl.com/generate/images/1069105/2026-03-29/0d145037-f933-4d82-9977-95054238f44a.png";

export default function LandingPage() {
  const navigate = useNavigate();
  const { isAuthenticated } = useAuth();
  const [billingPeriod, setBillingPeriod] = useState<"monthly" | "annual">("annual");
  const [isCreating, setIsCreating] = useState(false);

  const handleStartBuilding = useCallback(async () => {
    if (!isAuthenticated) {
      navigate("/register");
      return;
    }

    setIsCreating(true);
    try {
      const now = new Date().toISOString();

      // Get per-user workspace number
      let workspaceNum = 1;
      try {
        const projectsRes = await client.entities.projects.query({
          query: { status: "active" },
          limit: 50,
        });
        if (projectsRes?.data?.items?.length > 0) {
          workspaceNum = Math.max(...projectsRes.data.items.map((p: Record<string, unknown>) => (p.project_number as number) || 0)) + 1;
        }
      } catch {
        // fallback: use generic name
      }

      const res = await client.entities.projects.create({
        data: {
          name: `Workspace ${workspaceNum}`,
          description: "Created from landing page",
          status: "active",
          visibility: "private",
          framework: "react",
          created_at: now,
          updated_at: now,
        },
      });
      if (res?.data?.id) {
        toast.success("Project created! Opening workspace...");
        navigate(`/workspace/${res.data.project_number}`);
      } else {
        navigate("/dashboard");
      }
    } catch (err) {
      console.error("Failed to create project:", err);
      toast.error("Failed to create project, redirecting to dashboard...");
      navigate("/dashboard");
    } finally {
      setIsCreating(false);
    }
  }, [isAuthenticated, navigate]);

  const pricingPlans = [
    {
      name: "Free",
      price: "$0",
      period: "/ month",
      description: "For getting started",
      features: [
        "15 daily credits (Up to 25/month)",
        "2GB disk space",
        "Unlimited project sharing",
        "2 Atoms Cloud projects",
      ],
    },
    {
      name: "Pro",
      price: billingPeriod === "annual" ? "$15.8" : "$20",
      originalPrice: billingPeriod === "annual" ? "$20" : undefined,
      period: "/ month",
      description: "Unlock more features",
      features: [
        "100 credits per month",
        "10GB disk space",
        "Private projects",
        "Download projects",
        "Edit projects",
        "Credits rollovers",
        "Unlimited Atoms Cloud projects",
        "Custom domain",
      ],
    },
    {
      name: "Max",
      price: billingPeriod === "annual" ? "$79" : "$100",
      originalPrice: billingPeriod === "annual" ? "$100" : undefined,
      period: "/ month",
      description: "Full access to the best of Atoms",
      highlighted: true,
      badge: "Recommend",
      features: [
        "500 credits per month",
        "100GB disk space",
        "Private projects",
        "Download projects",
        "Edit projects",
        "Credits rollovers",
        "Unlimited Atoms Cloud projects",
        "2x compute resources",
        "Race mode",
        "Custom domain",
      ],
    },
  ];

  const faqs = [
    {
      q: "What's the difference between Free, Pro, and Max?",
      a: "Free gives you basic access with 25 credits/month. Pro unlocks 100 credits, private projects, and more storage. Max provides 500 credits, 2x compute resources, race mode, and full platform access.",
    },
    {
      q: "Can I switch plans at any time?",
      a: "Yes! You can upgrade or downgrade your plan at any time. Changes take effect immediately for upgrades, and at the end of your billing cycle for downgrades.",
    },
    {
      q: "Can I buy extra credits if I run out?",
      a: "Yes, Pro and Max users can purchase additional credit packs. Check the pricing page for available options.",
    },
    {
      q: "Will my unused credits be reset to zero?",
      a: "Pro and Max plans include credit rollovers, so your unused credits carry over to the next month. Free plan credits reset monthly.",
    },
    {
      q: "What are credits and how are they used?",
      a: "Credits are consumed when you interact with AI agents to build, edit, or deploy projects. Each message or action uses a certain number of credits based on complexity.",
    },
    {
      q: "Are subscriptions auto-renewed?",
      a: "Yes, all paid subscriptions auto-renew at the end of each billing cycle. You can cancel anytime from your account settings.",
    },
  ];

  const features = [
    {
      icon: <Bot className="w-6 h-6" />,
      title: "AI Agent Team",
      description:
        "A team of specialized AI agents — Engineer, Designer, PM, Data Analyst — collaborate to build your project from idea to deployment.",
      image: FEATURE_AI,
    },
    {
      icon: <Code2 className="w-6 h-6" />,
      title: "Smart Code Editor",
      description:
        "Real-time code generation with syntax highlighting, file management, and intelligent suggestions powered by multiple LLM models.",
      image: FEATURE_CODE,
    },
    {
      icon: <Eye className="w-6 h-6" />,
      title: "Live App Viewer",
      description:
        "Preview your application in real-time as it's being built. Replace elements, add images, and publish with one click.",
      image: FEATURE_CODE,
    },
    {
      icon: <Rocket className="w-6 h-6" />,
      title: "One-Click Deploy",
      description:
        "Deploy your projects instantly to production with custom domains, SSL, and global CDN. No DevOps knowledge required.",
      image: FEATURE_DEPLOY,
    },
  ];

  return (
    <div className="min-h-screen bg-[#09090B] text-white">
      <Navbar />

      {/* Hero Section */}
      <section className="relative min-h-screen flex items-center justify-center overflow-hidden">
        {/* Background */}
        <div className="absolute inset-0">
          <img
            src={HERO_BG}
            alt=""
            className="w-full h-full object-cover opacity-40"
          />
          <div className="absolute inset-0 bg-gradient-to-b from-[#09090B]/60 via-transparent to-[#09090B]" />
        </div>

        {/* Animated gradient orbs */}
        <div className="absolute top-1/4 left-1/4 w-96 h-96 bg-[#7C3AED]/20 rounded-full blur-[128px] animate-pulse" />
        <div className="absolute bottom-1/4 right-1/4 w-96 h-96 bg-[#A855F7]/15 rounded-full blur-[128px] animate-pulse delay-1000" />

        <div className="relative z-10 max-w-5xl mx-auto px-6 text-center">
          <div className="inline-flex items-center gap-2 bg-[#18181B]/80 border border-[#27272A] rounded-full px-4 py-2 mb-8 backdrop-blur-sm">
            <Sparkles className="w-4 h-4 text-[#A855F7]" />
            <span className="text-sm text-[#A1A1AA]">
              Powered by AI Agent Technology
            </span>
          </div>

          <h1 className="text-5xl md:text-7xl font-bold leading-tight mb-6">
            Turn ideas into
            <br />
            <span className="bg-gradient-to-r from-[#7C3AED] via-[#A855F7] to-[#C084FC] bg-clip-text text-transparent">
              software
            </span>
          </h1>

          <p className="text-lg md:text-xl text-[#A1A1AA] max-w-2xl mx-auto mb-10 leading-relaxed">
            Atoms is an AI-powered development platform where a team of
            intelligent agents collaborates to build, test, and deploy your
            applications — from idea to production in minutes.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <Button
              size="lg"
              className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] text-white hover:opacity-90 border-0 px-8 h-12 text-base"
              onClick={handleStartBuilding}
              disabled={isCreating}
            >
              {isCreating ? (
                <>
                  <Loader2 className="w-4 h-4 mr-2 animate-spin" />
                  Creating Project...
                </>
              ) : (
                <>
                  Start Building Free
                  <ArrowRight className="w-4 h-4 ml-2" />
                </>
              )}
            </Button>
            <Link to="/explore">
              <Button
                size="lg"
                variant="outline"
                className="border-[#27272A] text-white hover:bg-[#18181B] px-8 h-12 text-base bg-transparent"
              >
                Explore Projects
              </Button>
            </Link>
          </div>

          {/* Stats */}
          <div className="flex items-center justify-center gap-8 md:gap-16 mt-16">
            {[
              { value: "100K+", label: "Projects Built" },
              { value: "50K+", label: "Active Users" },
              { value: "99.9%", label: "Uptime" },
            ].map((stat, i) => (
              <div key={i} className="text-center">
                <div className="text-2xl md:text-3xl font-bold text-white">
                  {stat.value}
                </div>
                <div className="text-sm text-[#71717A]">{stat.label}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Features Section */}
      <section className="py-24 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <div className="inline-flex items-center gap-2 bg-[#7C3AED]/10 border border-[#7C3AED]/20 rounded-full px-4 py-1.5 mb-4">
              <Zap className="w-4 h-4 text-[#7C3AED]" />
              <span className="text-sm text-[#A855F7]">Core Features</span>
            </div>
            <h2 className="text-4xl md:text-5xl font-bold mb-4">
              Everything you need to{" "}
              <span className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] bg-clip-text text-transparent">
                build fast
              </span>
            </h2>
            <p className="text-[#A1A1AA] text-lg max-w-2xl mx-auto">
              From AI-powered code generation to one-click deployment, Atoms
              provides a complete development workflow.
            </p>
          </div>

          <div className="grid md:grid-cols-2 gap-6">
            {features.map((feature, i) => (
              <div
                key={i}
                className="group relative bg-[#18181B] border border-[#27272A] rounded-2xl overflow-hidden hover:border-[#7C3AED]/30 transition-all duration-500"
              >
                <div className="aspect-video overflow-hidden">
                  <img
                    src={feature.image}
                    alt={feature.title}
                    className="w-full h-full object-cover opacity-60 group-hover:opacity-80 group-hover:scale-105 transition-all duration-700"
                  />
                  <div className="absolute inset-0 bg-gradient-to-t from-[#18181B] via-[#18181B]/50 to-transparent" />
                </div>
                <div className="relative p-6 -mt-12">
                  <div className="w-12 h-12 rounded-xl bg-[#7C3AED]/10 border border-[#7C3AED]/20 flex items-center justify-center text-[#A855F7] mb-4">
                    {feature.icon}
                  </div>
                  <h3 className="text-xl font-semibold text-white mb-2">
                    {feature.title}
                  </h3>
                  <p className="text-[#A1A1AA] leading-relaxed">
                    {feature.description}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Capabilities Section */}
      <section className="py-24 px-6 bg-[#0D0D0F]">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-16">
            <h2 className="text-4xl md:text-5xl font-bold mb-4">
              Built for{" "}
              <span className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] bg-clip-text text-transparent">
                modern teams
              </span>
            </h2>
            <p className="text-[#A1A1AA] text-lg max-w-2xl mx-auto">
              Whether you're a solo developer or a growing startup, Atoms scales
              with your needs.
            </p>
          </div>

          <div className="grid md:grid-cols-3 gap-6">
            {[
              {
                icon: <Users className="w-6 h-6" />,
                title: "Multi-Agent Collaboration",
                desc: "AI agents work together as a team — engineer, designer, PM, and data analyst — each contributing their expertise.",
              },
              {
                icon: <Shield className="w-6 h-6" />,
                title: "Enterprise Security",
                desc: "Private projects, secure deployments, and data protection. Your code and data stay safe.",
              },
              {
                icon: <Globe className="w-6 h-6" />,
                title: "Global Deployment",
                desc: "Deploy to a global CDN with custom domains and SSL. Your apps load fast everywhere.",
              },
              {
                icon: <Zap className="w-6 h-6" />,
                title: "Race Mode",
                desc: "Run multiple AI models simultaneously and pick the best result. Available on Max plan.",
              },
              {
                icon: <Code2 className="w-6 h-6" />,
                title: "Multiple LLMs",
                desc: "Choose from Claude, GPT, Gemini, DeepSeek, and Qwen. Switch models anytime.",
              },
              {
                icon: <Rocket className="w-6 h-6" />,
                title: "Atoms Cloud",
                desc: "Built-in backend with auth, database, storage, and edge functions. Zero config needed.",
              },
            ].map((item, i) => (
              <div
                key={i}
                className="bg-[#18181B] border border-[#27272A] rounded-xl p-6 hover:border-[#7C3AED]/30 transition-all duration-300 group"
              >
                <div className="w-10 h-10 rounded-lg bg-[#7C3AED]/10 flex items-center justify-center text-[#A855F7] mb-4 group-hover:bg-[#7C3AED]/20 transition-colors">
                  {item.icon}
                </div>
                <h3 className="text-lg font-semibold text-white mb-2">
                  {item.title}
                </h3>
                <p className="text-[#A1A1AA] text-sm leading-relaxed">
                  {item.desc}
                </p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing Section */}
      <section id="pricing" className="py-24 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="text-center mb-12">
            <h2 className="text-4xl md:text-5xl font-bold mb-4">Pricing</h2>
            <p className="text-[#A1A1AA] text-lg">
              Free to start. Flexible to scale.
            </p>

            {/* Billing Toggle */}
            <div className="inline-flex items-center bg-[#18181B] border border-[#27272A] rounded-full p-1 mt-8">
              <button
                onClick={() => setBillingPeriod("monthly")}
                className={`px-5 py-2 rounded-full text-sm transition-all ${
                  billingPeriod === "monthly"
                    ? "bg-[#27272A] text-white"
                    : "text-[#A1A1AA] hover:text-white"
                }`}
              >
                Monthly
              </button>
              <button
                onClick={() => setBillingPeriod("annual")}
                className={`px-5 py-2 rounded-full text-sm transition-all flex items-center gap-2 ${
                  billingPeriod === "annual"
                    ? "bg-[#27272A] text-white"
                    : "text-[#A1A1AA] hover:text-white"
                }`}
              >
                Annual
                <span className="text-xs bg-[#7C3AED]/20 text-[#A855F7] px-2 py-0.5 rounded-full">
                  Save 21%
                </span>
              </button>
            </div>
          </div>

          <div className="grid md:grid-cols-3 gap-6 max-w-5xl mx-auto">
            {pricingPlans.map((plan, i) => (
              <PricingCard key={i} {...plan} />
            ))}
          </div>
        </div>
      </section>

      {/* FAQ Section */}
      <section className="py-24 px-6 bg-[#0D0D0F]">
        <div className="max-w-3xl mx-auto">
          <div className="text-center mb-12">
            <h2 className="text-4xl font-bold mb-4">
              Frequently Asked Questions
            </h2>
            <p className="text-[#A1A1AA]">
              See our{" "}
              <a href="#" className="text-[#A855F7] hover:underline">
                Help center
              </a>{" "}
              for more info
            </p>
          </div>

          <Accordion type="single" collapsible className="space-y-3">
            {faqs.map((faq, i) => (
              <AccordionItem
                key={i}
                value={`faq-${i}`}
                className="bg-[#18181B] border border-[#27272A] rounded-xl px-6 data-[state=open]:border-[#7C3AED]/30"
              >
                <AccordionTrigger className="text-white text-left hover:no-underline py-5">
                  {faq.q}
                </AccordionTrigger>
                <AccordionContent className="text-[#A1A1AA] pb-5">
                  {faq.a}
                </AccordionContent>
              </AccordionItem>
            ))}
          </Accordion>
        </div>
      </section>

      {/* CTA Section */}
      <section className="py-24 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-4xl md:text-5xl font-bold mb-6">
            Ready to build with{" "}
            <span className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] bg-clip-text text-transparent">
              AI?
            </span>
          </h2>
          <p className="text-[#A1A1AA] text-lg mb-8 max-w-2xl mx-auto">
            Join thousands of developers and teams building the future with
            Atoms. Start for free, no credit card required.
          </p>
          <Button
            size="lg"
            className="bg-gradient-to-r from-[#7C3AED] to-[#A855F7] text-white hover:opacity-90 border-0 px-10 h-14 text-lg"
            onClick={handleStartBuilding}
            disabled={isCreating}
          >
            {isCreating ? (
              <>
                <Loader2 className="w-5 h-5 mr-2 animate-spin" />
                Creating Project...
              </>
            ) : (
              <>
                Get Started Free
                <ArrowRight className="w-5 h-5 ml-2" />
              </>
            )}
          </Button>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-[#27272A] py-16 px-6">
        <div className="max-w-7xl mx-auto">
          <div className="grid grid-cols-2 md:grid-cols-5 gap-8">
            {/* Brand */}
            <div className="col-span-2 md:col-span-1">
              <div className="flex items-center gap-2 mb-4">
                <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#7C3AED] to-[#A855F7] flex items-center justify-center">
                  <span className="text-white font-bold text-sm">A</span>
                </div>
                <span className="text-white font-semibold text-lg">Atoms</span>
              </div>
              <p className="text-[#71717A] text-sm">
                Turn ideas into products that sell
              </p>
            </div>

            {/* Product */}
            <div>
              <h4 className="text-[#71717A] text-xs uppercase tracking-wider font-semibold mb-4">
                Product
              </h4>
              <ul className="space-y-2">
                {["Pricing", "Help Center"].map((item) => (
                  <li key={item}>
                    <a
                      href="#"
                      className="text-[#A1A1AA] text-sm hover:text-white transition-colors"
                    >
                      {item}
                    </a>
                  </li>
                ))}
              </ul>
            </div>

            {/* Resources */}
            <div>
              <h4 className="text-[#71717A] text-xs uppercase tracking-wider font-semibold mb-4">
                Resources
              </h4>
              <ul className="space-y-2">
                {["Blog", "Use Cases", "Videos", "GitHub"].map((item) => (
                  <li key={item}>
                    <a
                      href="#"
                      className="text-[#A1A1AA] text-sm hover:text-white transition-colors"
                    >
                      {item}
                    </a>
                  </li>
                ))}
              </ul>
            </div>

            {/* About */}
            <div>
              <h4 className="text-[#71717A] text-xs uppercase tracking-wider font-semibold mb-4">
                About
              </h4>
              <ul className="space-y-2">
                {[
                  "MetaGPT",
                  "OpenManus",
                  "Foundation Agents",
                  "Privacy Policy",
                  "Terms of Service",
                ].map((item) => (
                  <li key={item}>
                    <a
                      href="#"
                      className="text-[#A1A1AA] text-sm hover:text-white transition-colors"
                    >
                      {item}
                    </a>
                  </li>
                ))}
              </ul>
            </div>

            {/* Community */}
            <div>
              <h4 className="text-[#71717A] text-xs uppercase tracking-wider font-semibold mb-4">
                Community
              </h4>
              <ul className="space-y-2">
                {[
                  "Affiliates",
                  "Explorer Program",
                  "X / Twitter",
                  "LinkedIn",
                  "Discord",
                ].map((item) => (
                  <li key={item}>
                    <a
                      href="#"
                      className="text-[#A1A1AA] text-sm hover:text-white transition-colors"
                    >
                      {item}
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          </div>

          <div className="border-t border-[#27272A] mt-12 pt-8 flex flex-col md:flex-row items-center justify-between gap-4">
            <p className="text-[#71717A] text-sm">
              © 2024 Atoms. All rights reserved.
            </p>
            <div className="flex items-center gap-4">
              {["X", "LinkedIn", "Discord", "GitHub", "Reddit"].map((social) => (
                <a
                  key={social}
                  href="#"
                  className="w-8 h-8 rounded-lg bg-[#18181B] border border-[#27272A] flex items-center justify-center text-[#71717A] hover:text-white hover:border-[#3F3F46] transition-all text-xs"
                >
                  {social[0]}
                </a>
              ))}
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
