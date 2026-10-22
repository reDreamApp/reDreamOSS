"""
Block A: The Knowledge Graph

Clinical ontology based on:
- MMS (Mastery Measurement Scale) - Germain et al., 2004: Adaptive behaviors/goals
- IRT clinical guidelines - Krakow & Zadra, 2006: Maladaptive behaviors/risks
- DSM-5 PTSD criteria - APA, 2013: Trauma-related avoidance and hyperarousal
- Germain et al., 2004 (MMS Subscale VI): Avoidance coding

This module creates a directed graph representing the clinical knowledge base
for the safety layer, with correction edges from maladaptive to adaptive nodes.

REFERENCES:
-----------
1. Germain, A., Krakow, B., Faucher, B., et al. (2004). "Increased Mastery Elements
   Associated With Imagery Rehearsal Treatment for Nightmares in Sexual Assault
   Survivors With PTSD." Dreaming, 14(4), 195-206.

2. Krakow, B., & Zadra, A. (2006). "Clinical management of chronic nightmares:
   Imagery Rehearsal Therapy." Behavioral Sleep Medicine, 4(1), 45-70.

3. American Psychiatric Association. (2013). Diagnostic and Statistical Manual of
   Mental Disorders (5th ed.). DSM-5 PTSD Criterion C (Avoidance) & E (Hyperarousal).

4. Rousseau, A., & Belleville, G. (2018). "The mechanisms of action underlying the
   efficacy of psychological nightmare treatments." Sleep Medicine Reviews.

5. Nielsen, T., & Levin, R. (2007). "Nightmares: A new neurocognitive model."
   Sleep Medicine Reviews. (AMPHAC model - fear extinction requires engagement, not avoidance)
"""

import networkx as nx
from typing import Dict, List, Any


def build_clinical_graph() -> nx.DiGraph:
    """
    Build and return the clinical knowledge graph.
    
    The graph contains:
    - Maladaptive nodes (risks) from IRT clinical guidelines
    - Adaptive nodes (goals) from MMS (all 5 mastery types)
    - Correction edges from risks to goals with clinical rationale
    
    Returns:
        nx.DiGraph: The clinical knowledge graph
    """
    G = nx.DiGraph()
    
    # =========================================================================
    # ADAPTIVE NODES (Goals) - Source: MMS Manual (Germain et al., 2004)
    # Reference: "Increased Mastery Elements Associated With IRT..." Dreaming 14(4)
    # =========================================================================
    
    adaptive_nodes = {
        "BEHAVIORAL_MASTERY": {
            "type": "adaptive",
            "category": "mastery",
            "mms_subscale": "I",
            "description": "Active behavioral response to threat or challenge. The dreamer takes direct action to address the situation.",
            "mms_category": "Action",
            "reference": "Germain et al. (2004) MMS Subscale I: 'The dreamer actively performs an action to change the dream course to their advantage.'",
            "nli_hypothesis": "The dreamer directly confronts, fights, or physically resists the threat.",
            "scoring_criteria": [
                "Performs an action to alter the course of the dream to the advantage of the dreamer",
                "Fights back against a threat",
                "Wins over a threat or aggressor",
                "Makes behavioral attempts to obtain assistance"
            ],
            "examples": [
                "Fighting back against an attacker",
                "Running away purposefully",
                "Confronting the threat directly",
                "Taking control of the situation"
            ]
        },
        "SOCIAL_MASTERY": {
            "type": "adaptive",
            "category": "mastery",
            "mms_subscale": "II",
            "description": "Seeking or receiving help from others. The dreamer engages social support to resolve the situation.",
            "mms_category": "Help",
            "reference": "Germain et al. (2004) MMS Subscale II: 'Changes related to other characters, social interactions, or receiving help.'",
            "nli_hypothesis": "The dreamer asks another person for help or receives assistance from someone else.",
            "scoring_criteria": [
                "Changes the personality aspects of other dream characters",
                "Removes threatening characters",
                "Changes the nature of an interaction with another character",
                "Adds a helpful new character",
                "Adds a non-threatening social setting",
                "Is assisted by another character on the dreamer's request"
            ],
            "examples": [
                "Calling for help",
                "A friend or ally appearing to assist",
                "Working together with others",
                "Receiving protection from someone"
            ]
        },
        "ENVIRONMENTAL_MASTERY": {
            "type": "adaptive",
            "category": "mastery",
            "mms_subscale": "III",
            "description": "Changes to the physical setting or spontaneous helpful events. The environment becomes safe or provides resources.",
            "mms_category": "Environment/Tools",
            "reference": "Germain et al. (2004) MMS Subscale III: 'Changes to the physical setting or spontaneous helpful events.'",
            "nli_hypothesis": "The dreamer finds a useful object like a weapon or tool, or the surroundings change to become safe.",
            "scoring_criteria": [
                "Changes the physical environment to a non-threatening setting",
                "Makes the initial dream environment impermeable to threat",
                "Experiences a spontaneous occurrence of a non-requested helpful event"
            ],
            "examples": [
                "Finding a weapon or tool",
                "The environment changing favorably",
                "Discovering a hidden escape route",
                "A door appearing that wasn't there before"
            ]
        },
        "EMOTIONAL_MASTERY": {
            "type": "adaptive",
            "category": "mastery",
            "mms_subscale": "IV",
            "description": "Self-soothing and emotional regulation. The dreamer manages their emotional state to stay calm and engaged.",
            "mms_category": "Self-soothing",
            "reference": "Germain et al. (2004) MMS Subscale IV: 'Changes in the dreamer's emotional state or reaction.'",
            "nli_hypothesis": "The dreamer manages their emotions by calming down, breathing deeply, or staying present despite fear.",
            "scoring_criteria": [
                "Changes the overall dream affect (emotional atmosphere)",
                "Changes their emotional reactions to specific dream characters",
                "Changes their emotional reactions to specific dream events",
                "Changes their emotional reactions to specific dream settings"
            ],
            "examples": [
                "Calming oneself down",
                "Accepting the situation peacefully",
                "Transforming fear into curiosity",
                "Remaining present despite fear"
            ]
        },
        "MYTHICAL_MASTERY": {
            "type": "adaptive",
            "category": "mastery",
            "mms_subscale": "V",
            "description": "Supernatural or divine intervention. The dreamer uses magical powers or receives divine help to overcome the threat.",
            "mms_category": "Supernatural/Divine",
            "reference": "Germain et al. (2004) MMS Subscale V: 'Interventions involving supernatural or divine elements.'",
            "nli_hypothesis": "The dreamer gains supernatural abilities, uses magic or superpowers, flies, or receives help from a divine being.",
            "scoring_criteria": [
                "A supernatural figure intervenes in favor of the dreamer to release them from the threat",
                "A supernatural event intervenes to terminate the threat",
                "A supernatural figure/event reassures the dreamer (even without changing the course of the dream)",
                "The dreamer uses supernatural powers to overcome the threat"
            ],
            "examples": [
                "Using magical powers to defeat the threat",
                "An angel or divine figure appearing to help",
                "Gaining supernatural abilities (flying, super strength)",
                "A miraculous event saving the dreamer"
            ]
        }
    }
    
    # Add adaptive nodes to graph
    for node_id, attrs in adaptive_nodes.items():
        G.add_node(node_id, **attrs)
    
    # =========================================================================
    # MALADAPTIVE NODES (Risks) - Sources: IRT clinical guidelines (Krakow & Zadra, 2006),
    # DSM-5 PTSD criteria (APA, 2013), Germain et al. (2004) MMS Subscale VI
    # =========================================================================
    
    maladaptive_nodes = {
        "AVOIDANCE": {
            "type": "maladaptive",
            "category": "avoidance",
            "severity": "high",
            "description": "The dreamer avoids interaction to regulate anxiety. This includes passive responses that prevent engagement with the dream scenario.",
            "clinical_concept": "Avoidance / Safety-seeking behavior",
            "reference": "Krakow & Zadra (2006): IRT targets avoidance by replacing passive coping with active mastery. DSM-5 Criterion C: persistent avoidance of trauma-related stimuli.",
            "clinical_guideline": "Avoidance prevents therapeutic processing. The patient should be guided toward active engagement with the dream content.",
            "why_maladaptive": "Nielsen & Levin (2007) AMPHAC model: Nightmares persist due to failed fear extinction. Avoidance prevents extinction learning. Rousseau & Belleville (2018): 'Prevention of Avoidance' is a key mechanism of nightmare treatment.",
            "nli_hypothesis": "The dreamer hides, freezes, does nothing, or avoids confrontation with the threat.",
            "behavioral_markers": [
                "Waking up to escape",
                "Freezing in place",
                "Hiding from the threat",
                "Looking away or closing eyes",
                "Becoming invisible or disappearing",
                "Passive observation without action"
            ]
        },
        "INTERRUPTION": {
            "type": "maladaptive",
            "category": "escape",
            "severity": "high",
            "description": "Breaking the dream narrative completely to stop the affect. A forced termination that prevents any resolution.",
            "clinical_concept": "Dream interruption / Premature termination",
            "reference": "Germain et al. (2004) MMS Subscale VI: 'Awakens from the dream to escape perceived threats' is coded as Avoidance, not Mastery.",
            "clinical_guideline": "Interrupting the dream prevents emotional processing. The patient should be guided to stay within the dream and find resolution.",
            "why_maladaptive": "MMS Manual (Germain et al., 2004) Subscale VI (Avoidance): 'Awakens from the dream to escape perceived threats' is coded as Avoidance, not Mastery. It prevents the cognitive restructuring that IRT aims to achieve.",
            "nli_hypothesis": "The dreamer wakes up from the dream or makes the dream end abruptly.",
            "behavioral_markers": [
                "Forcing oneself to wake up",
                "The dream suddenly ending",
                "Scene dissolving without resolution",
                "Abrupt narrative breaks",
                "Refusing to continue the dream scenario"
            ]
        },
        "VIOLENT_REVENGE": {
            "type": "maladaptive",
            "category": "violence",
            "severity": "critical",
            "description": "Uncontrolled, chaotic violence that destroys the scene rather than resolving it. Represents disproportionate aggression rather than adaptive mastery.",
            "clinical_concept": "Violent revenge fantasy / Disproportionate aggression",
            "reference": "Krakow & Zadra (2006): IRT explicitly discourages violent revenge fantasies; the goal is mastery, not destruction. DSM-5 Criterion E2: irritable behavior and angry outbursts as hyperarousal symptom.",
            "clinical_guideline": "Extreme violence is a primitive defense that prevents adaptive coping. Guide toward controlled, purposeful action instead of destructive chaos.",
            "why_maladaptive": "IRT guidelines explicitly discourage violent revenge fantasies. Disproportionate violence parallels trauma responses where overwhelming affect leads to primitive fight responses rather than adaptive problem-solving. The goal of IRT is mastery and resolution, not destruction.",
            "nli_hypothesis": "The dreamer kills everyone, murders people, or destroys everything around them.",
            "behavioral_markers": [
                "Killing everyone in the dream",
                "Excessive brutal violence",
                "Destroying the entire dream environment",
                "Uncontrolled rage responses",
                "Violence disproportionate to the threat"
            ]
        },
        "SUPPRESSION": {
            "type": "maladaptive",
            "category": "suppression",
            "severity": "high",
            "description": "Emotional denial or numbing in the face of distressing dream content. The dreamer claims to feel nothing or denies emotional impact when the nightmare context clearly warrants distress. Context-dependent: requires distressing content to distinguish from genuine emotional mastery.",
            "clinical_concept": "Emotional suppression / Denial of affect",
            "reference": "DSM-5 Criterion C: persistent avoidance includes emotional numbing and detachment. Nielsen & Levin (2007): AMPHAC model - fear extinction requires emotional engagement, not suppression.",
            "clinical_guideline": "Emotional suppression prevents therapeutic processing. The patient should be guided toward acknowledging and working through emotions, not denying them.",
            "why_maladaptive": "DSM-5 Criterion C identifies emotional numbing as a form of avoidance. Nielsen & Levin (2007) AMPHAC model: fear extinction requires active emotional engagement. Suppressing emotions in response to distressing nightmare content prevents the affective processing that IRT requires for therapeutic change.",
            "nli_hypothesis": "The dreamer claims to feel nothing, denies being affected, or suppresses emotional responses to distressing content.",
            "behavioral_markers": [
                "Claiming to feel nothing about distressing content",
                "Denying emotional impact ('I'm fine with it')",
                "Emotional numbing ('It doesn't bother me')",
                "Detachment from clearly distressing scenarios",
                "Suppressing natural emotional responses"
            ]
        },
        "TRAUMA_REPLAY": {
            "type": "maladaptive",
            "category": "replay",
            "severity": "critical",
            "description": "Re-experiencing the traumatic nightmare content exactly as it happened without any transformation or change. The dreamer replays the trauma identically rather than rescripting it, which can reinforce traumatic associations.",
            "clinical_concept": "Trauma replay / Replicative nightmare rehearsal",
            "reference": "Krakow et al. (2001) JAMA RCT: rescripting replicative nightmares without change can worsen PTSD. Krakow & Zadra (2006): IRT explicitly contraindicates trauma replay rescripting. Germain (2013): 40-60% of PTSD patients have exact trauma replays. DSM-5 Criterion B2: recurrent distressing dreams with content related to the traumatic event.",
            "clinical_guideline": "Trauma replay without transformation reinforces traumatic associations. The patient must be guided to change something in the narrative - any modification breaks the replicative cycle.",
            "why_maladaptive": "Krakow et al. (2001) demonstrated that rehearsing nightmares without change can strengthen trauma memories. IRT's core mechanism is transformation: the patient must alter the nightmare to gain mastery. Replaying trauma identically bypasses this mechanism entirely and risks re-traumatization.",
            "nli_hypothesis": "The dreamer replays the traumatic event exactly as it happened, refuses to change anything, or re-experiences the nightmare without transformation.",
            "behavioral_markers": [
                "Replaying trauma exactly as it happened",
                "Refusing to change the nightmare narrative",
                "Re-experiencing without any transformation",
                "Rehearsing the nightmare identically",
                "Reliving the same events without alteration"
            ]
        }
    }
    
    # Add maladaptive nodes to graph
    for node_id, attrs in maladaptive_nodes.items():
        G.add_node(node_id, **attrs)
    
    # =========================================================================
    # NEUTRAL NODES - For narrative descriptions that are neither adaptive nor maladaptive
    # These handle cases where the user describes settings, feelings, or context
    # without taking action - they should be validated but not corrected
    # =========================================================================
    
    # =========================================================================
    # UNCLASSIFIED NODE - For inputs that could not be reliably classified
    # This node acts as a fail-safe: when confidence is below the threshold,
    # the critic routes the segment here instead of silently assigning a
    # potentially wrong label. The threshold starts at 0 (catching only
    # total classification failures) and can be raised based on experiments.
    # =========================================================================

    unclassified_node = {
        "UNCLASSIFIED": {
            "type": "unclassified",
            "category": "unknown",
            "description": "Input could not be reliably classified. The classifier's confidence was below the threshold, indicating the text did not clearly match any clinical category.",
            "system_action": "seek_clarification",
            "clinical_guideline": "When the system cannot reliably classify user input, seek clarification rather than making assumptions. Ask the user to elaborate on their intended dream action.",
        }
    }

    for node_id, attrs in unclassified_node.items():
        G.add_node(node_id, **attrs)

    neutral_nodes = {
        "NARRATIVE_SETTING": {
            "type": "neutral",
            "category": "narrative",
            "description": "Description of environment, characters, or setting without active interaction. The dreamer describes the scene without taking action.",
            "system_action": "validate_context",
            "reference": "Contextual descriptions that set the stage for dream content without indicating adaptive or maladaptive behavior.",
            "nli_hypothesis": "The dreamer sees or observes something without doing anything about it.",
            "examples": [
                "There was a dark forest",
                "I saw a monster in the corner",
                "The room was cold and empty",
                "A figure appeared in the doorway"
            ]
        },
        "AFFECT_EXPRESSION": {
            "type": "neutral",
            "category": "affect",
            "description": "Explicit statement of emotion without accompanying action. The dreamer expresses feelings without behavioral response.",
            "system_action": "empathy_validation",
            "reference": "Emotional expressions that indicate affect awareness - important for therapeutic engagement but not indicative of adaptive/maladaptive coping.",
            "nli_hypothesis": "The dreamer expresses fear, sadness, or other emotions without doing anything else.",
            "examples": [
                "I felt scared",
                "I was terrified",
                "I felt angry",
                "I was overwhelmed with fear"
            ]
        }
    }
    
    # Add neutral nodes to graph
    for node_id, attrs in neutral_nodes.items():
        G.add_node(node_id, **attrs)
    
    # =========================================================================
    # CORRECTION EDGES (Risk -> Goal)
    # Each edge includes clinical rationale and references
    # =========================================================================
    
    correction_edges = [
        # =====================================================================
        # AVOIDANCE corrections
        # =====================================================================

        # Primary: Avoidance -> Behavioral Mastery (Action)
        (
            "AVOIDANCE",
            "BEHAVIORAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "primary",
                "rationale": "Counter avoidance with active behavioral engagement. The core therapeutic goal of IRT is to replace passive avoidance with active coping.",
                "clinical_reference": "Rousseau & Belleville (2018): 'Prevention of avoidance' is listed as a key mechanism. Krakow & Zadra (2006): IRT works by 'changing the nightmare through active mastery rather than avoidance.'",
                "therapeutic_direction": "What action could you take in this situation? How could you respond actively instead of hiding?"
            }
        ),
        
        # Secondary: Avoidance -> Social Mastery (seek help)
        (
            "AVOIDANCE",
            "SOCIAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "secondary",
                "rationale": "Counter isolation with social engagement. If hiding alone, guide toward seeking support from others.",
                "clinical_reference": "Germain et al. (2004): Social Mastery includes 'making behavioral attempts to obtain assistance' - transforms passive hiding into active help-seeking.",
                "therapeutic_direction": "Is there someone who could help you in this situation? Who could you call for support?"
            }
        ),
        
        # Tertiary: Avoidance -> Mythical Mastery (supernatural empowerment)
        (
            "AVOIDANCE",
            "MYTHICAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "tertiary",
                "rationale": "For patients who feel powerless, supernatural empowerment can provide a sense of agency that counters avoidance.",
                "clinical_reference": "Germain et al. (2004): 'The dreamer uses supernatural powers to overcome the threat' - provides mastery through imagined empowerment.",
                "therapeutic_direction": "What if you had a special power in this dream? What ability would help you face this situation?"
            }
        ),
        
        # =====================================================================
        # INTERRUPTION (Escape/Wake up) corrections
        # =====================================================================

        # Primary: Interrupt -> Emotional Mastery (stay calm, stay present)
        (
            "INTERRUPTION",
            "EMOTIONAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "primary",
                "rationale": "Counter escape with emotional regulation. Help the patient stay present in the dream by managing their emotional state.",
                "clinical_reference": "The urge to wake up stems from overwhelming affect. Germain et al. (2004) Emotional Mastery: 'Changes their emotional reactions to specific dream events.' This correction is based on the mechanism of affect tolerance required to prevent the interrupt.",
                "therapeutic_direction": "How could you calm yourself to stay in the dream? What would help you feel safe enough to continue?"
            }
        ),
        
        # Secondary: Interrupt -> Environmental Mastery (find safety within dream)
        (
            "INTERRUPTION",
            "ENVIRONMENTAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "secondary",
                "rationale": "Instead of escaping the dream entirely, find safety within the dream through environmental changes.",
                "clinical_reference": "Germain et al. (2004): 'Makes the initial dream environment impermeable to threat' - creates safety without leaving the dream space.",
                "therapeutic_direction": "What in the environment could make you feel safe? Is there a place in the dream where you'd feel protected?"
            }
        ),
        
        # Tertiary: Interrupt -> Mythical Mastery (divine protection)
        (
            "INTERRUPTION",
            "MYTHICAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "tertiary",
                "rationale": "For patients who feel they cannot stay in the dream, supernatural protection provides a sense of safety that enables engagement.",
                "clinical_reference": "Germain et al. (2004): 'A supernatural figure/event reassures the dreamer' - provides safety without requiring escape.",
                "therapeutic_direction": "What if a protective presence was watching over you? What would make you feel safe enough to continue?"
            }
        ),
        
        # =====================================================================
        # VIOLENT_REVENGE (Violence) corrections
        # =====================================================================

        # Primary: Violence -> Behavioral Mastery (controlled, purposeful action)
        (
            "VIOLENT_REVENGE",
            "BEHAVIORAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "primary",
                "rationale": "Counter chaotic violence with controlled, purposeful action. Transform primitive aggression into adaptive assertion.",
                "clinical_reference": "The key distinction is between 'regressive' (chaotic, destructive) and 'progressive' (controlled, goal-directed) aggression. Germain et al. (2004): 'Fights back against a threat' and 'Wins over a threat' are adaptive when proportionate and controlled.",
                "therapeutic_direction": "What focused action could resolve this without destroying everything? How could you stop the threat without excessive violence?"
            }
        ),
        
        # Secondary: Violence -> Social Mastery (get help instead of fighting alone)
        (
            "VIOLENT_REVENGE",
            "SOCIAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "secondary",
                "rationale": "Instead of overwhelming violence, engage others to help address the threat collectively. This is a clinical heuristic: isolation amplifies primitive defenses.",
                "clinical_reference": "Germain et al. (2004): 'Is assisted by another character' - shares the burden and prevents isolated destructive response.",
                "therapeutic_direction": "Who could help you deal with this threat? What if you didn't have to handle this alone?"
            }
        ),
        
        # Tertiary: Violence -> Emotional Mastery (regulate the rage)
        (
            "VIOLENT_REVENGE",
            "EMOTIONAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "tertiary",
                "rationale": "Address the underlying affect driving the violent impulse through emotional regulation.",
                "clinical_reference": "Regressive aggression stems from overwhelming affect. Germain et al. (2004) Emotional Mastery: 'Changes the overall dream affect' - modulating the emotional intensity can prevent violent acting out.",
                "therapeutic_direction": "What feeling is driving the urge to destroy? How could you manage that feeling differently?"
            }
        ),

        # =====================================================================
        # SUPPRESSION (Emotional Denial) corrections
        # =====================================================================

        # Primary: Suppression -> Emotional Mastery (acknowledge emotions)
        (
            "SUPPRESSION",
            "EMOTIONAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "primary",
                "rationale": "Counter emotional denial with genuine emotional engagement. The patient needs to acknowledge and process feelings rather than suppress them.",
                "clinical_reference": "Nielsen & Levin (2007) AMPHAC model: fear extinction requires emotional engagement. Germain et al. (2004) Emotional Mastery: 'Changes their emotional reactions' - this requires acknowledging emotions first, not denying them.",
                "therapeutic_direction": "It's natural to feel upset about this. What emotions come up when you think about this scene? Can you let yourself feel them?"
            }
        ),

        # Secondary: Suppression -> Social Mastery (share feelings)
        (
            "SUPPRESSION",
            "SOCIAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "secondary",
                "rationale": "Counter emotional isolation by sharing feelings with others. Expressing emotions to another person counteracts suppression.",
                "clinical_reference": "Germain et al. (2004): Social Mastery includes interpersonal engagement. Sharing emotions with others is a direct counter to emotional numbing and denial.",
                "therapeutic_direction": "Is there someone in the dream you could share your feelings with? What would it feel like to tell someone how you really feel?"
            }
        ),

        # Tertiary: Suppression -> Behavioral Mastery (channel into action)
        (
            "SUPPRESSION",
            "BEHAVIORAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "tertiary",
                "rationale": "Transform emotional shutdown into active engagement. Taking action requires emotional investment, countering numbing.",
                "clinical_reference": "Germain et al. (2004): 'The dreamer actively performs an action to change the dream course' - active engagement inherently counters emotional detachment.",
                "therapeutic_direction": "Instead of shutting down, what action could you take right now? What would you do if you let yourself care about this?"
            }
        ),

        # =====================================================================
        # TRAUMA_REPLAY (Re-experiencing) corrections
        # =====================================================================

        # Primary: Trauma Replay -> Behavioral Mastery (intervene actively)
        (
            "TRAUMA_REPLAY",
            "BEHAVIORAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "primary",
                "rationale": "Break the replay cycle by taking different action. Any behavioral change disrupts the replicative pattern and introduces mastery.",
                "clinical_reference": "Krakow & Zadra (2006): IRT's core mechanism is changing the nightmare. Germain et al. (2004): 'The dreamer actively performs an action to change the dream course' - even small changes break the replay loop.",
                "therapeutic_direction": "What if you did something different this time? Even a small change can break the pattern. What action would you like to take?"
            }
        ),

        # Secondary: Trauma Replay -> Environmental Mastery (change setting)
        (
            "TRAUMA_REPLAY",
            "ENVIRONMENTAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "secondary",
                "rationale": "Change the environment to disrupt the replay. Altering the setting breaks the exact replication of the trauma.",
                "clinical_reference": "Germain et al. (2004): 'Changes the physical environment to a non-threatening setting' - environmental changes are often the easiest way to begin rescripting a replicative nightmare.",
                "therapeutic_direction": "What if the setting was different? Can you change something about where this happens? What would make this place feel different?"
            }
        ),

        # Tertiary: Trauma Replay -> Mythical Mastery (supernatural transformation)
        (
            "TRAUMA_REPLAY",
            "MYTHICAL_MASTERY",
            {
                "relation": "correction_strategy",
                "priority": "tertiary",
                "rationale": "Use supernatural elements to transform the replayed scenario. Impossible changes can be easier to imagine when the trauma feels unchangeable.",
                "clinical_reference": "Germain et al. (2004): 'A supernatural event intervenes to terminate the threat' - for replicative nightmares that feel impossible to change, supernatural elements provide a path to transformation.",
                "therapeutic_direction": "What if something magical or impossible happened to change this? What supernatural power could transform this situation?"
            }
        ),
    ]
    
    # Add correction edges to graph
    for source, target, attrs in correction_edges:
        G.add_edge(source, target, **attrs)
    
    return G


def get_all_node_ids(G: nx.DiGraph = None) -> List[str]:
    """Get all node IDs from the clinical graph."""
    if G is None:
        G = build_clinical_graph()
    return list(G.nodes())


def get_maladaptive_nodes(G: nx.DiGraph = None) -> Dict[str, Dict[str, Any]]:
    """Get all maladaptive (risk) nodes from the graph."""
    if G is None:
        G = build_clinical_graph()
    return {
        node_id: attrs 
        for node_id, attrs in G.nodes(data=True) 
        if attrs.get("type") == "maladaptive"
    }


def get_adaptive_nodes(G: nx.DiGraph = None) -> Dict[str, Dict[str, Any]]:
    """Get all adaptive (goal) nodes from the graph."""
    if G is None:
        G = build_clinical_graph()
    return {
        node_id: attrs 
        for node_id, attrs in G.nodes(data=True) 
        if attrs.get("type") == "adaptive"
    }


def get_neutral_nodes(G: nx.DiGraph = None) -> Dict[str, Dict[str, Any]]:
    """Get all neutral (narrative/affect) nodes from the graph."""
    if G is None:
        G = build_clinical_graph()
    return {
        node_id: attrs 
        for node_id, attrs in G.nodes(data=True) 
        if attrs.get("type") == "neutral"
    }


def get_correction_strategies(G: nx.DiGraph, maladaptive_node: str) -> List[Dict[str, Any]]:
    """Get all correction strategies for a given maladaptive node, ordered by priority."""
    if maladaptive_node not in G:
        return []
    
    strategies = []
    for _, target, edge_attrs in G.out_edges(maladaptive_node, data=True):
        if edge_attrs.get("relation") == "correction_strategy":
            target_attrs = G.nodes[target]
            strategies.append({
                "target_node": target,
                "target_description": target_attrs.get("description", ""),
                "priority": edge_attrs.get("priority", ""),
                "rationale": edge_attrs.get("rationale", ""),
                "clinical_reference": edge_attrs.get("clinical_reference", ""),
                "therapeutic_direction": edge_attrs.get("therapeutic_direction", "")
            })
    
    # Sort by priority
    priority_order = {"primary": 0, "secondary": 1, "tertiary": 2}
    strategies.sort(key=lambda x: priority_order.get(x["priority"], 99))
    
    return strategies


def print_ontology_with_references():
    """Print the full ontology with clinical references for verification."""
    G = build_clinical_graph()
    
    print("\n" + "=" * 80)
    print("CLINICAL ONTOLOGY WITH REFERENCES")
    print("=" * 80)
    
    print("\n📚 PRIMARY SOURCES:")
    print("-" * 80)
    print("""
    1. Germain et al. (2004) - MMS Manual
       "Increased Mastery Elements Associated With IRT..." Dreaming 14(4)
       → Defines the 6 Mastery subscales (our adaptive nodes)
       → Subscale VI (Avoidance) informs maladaptive coding

    2. Krakow & Zadra (2006) - IRT Clinical Guidelines
       "Clinical management of chronic nightmares: Imagery Rehearsal Therapy"
       → Defines maladaptive patterns: avoidance, interruption, violent revenge

    3. APA (2013) - DSM-5 PTSD Criteria
       Criterion C (Avoidance), Criterion E (Hyperarousal)
       → Clinical grounding for avoidance and aggression categories

    4. Rousseau & Belleville (2018) - Mechanisms of IRT
       "The mechanisms of action underlying nightmare treatments"
       → Identifies "Prevention of Avoidance" as key mechanism

    5. Nielsen & Levin (2007) - AMPHAC Model
       "Nightmares: A new neurocognitive model"
       → Nightmares = failed fear extinction; avoidance prevents extinction
    """)
    
    print("\n🔴 MALADAPTIVE NODES (from IRT clinical guidelines)")
    print("-" * 80)

    for node_id, attrs in get_maladaptive_nodes(G).items():
        print(f"\n  📌 {node_id}")
        print(f"     Clinical Concept: {attrs.get('clinical_concept', 'N/A')}")
        print(f"     Reference: {attrs.get('reference', 'N/A')}")
        print(f"     Why Maladaptive: {attrs.get('why_maladaptive', 'N/A')[:100]}...")
        
        print(f"\n     Correction Strategies:")
        for strategy in get_correction_strategies(G, node_id):
            print(f"       [{strategy['priority'].upper()}] -> {strategy['target_node']}")
            print(f"           Rationale: {strategy['rationale'][:80]}...")
            print(f"           Reference: {strategy['clinical_reference'][:80]}...")
    
    print("\n\n🟢 ADAPTIVE NODES (from MMS)")
    print("-" * 80)
    
    for node_id, attrs in get_adaptive_nodes(G).items():
        print(f"\n  📌 {node_id} (MMS Subscale {attrs.get('mms_subscale', 'N/A')})")
        print(f"     Reference: {attrs.get('reference', 'N/A')[:80]}...")
        print(f"     Scoring Criteria:")
        for criterion in attrs.get('scoring_criteria', [])[:2]:
            print(f"       - {criterion}")
    
    print("\n\n🟡 NEUTRAL NODES (Context/Affect)")
    print("-" * 80)
    
    for node_id, attrs in get_neutral_nodes(G).items():
        print(f"\n  📌 {node_id}")
        print(f"     Description: {attrs.get('description', 'N/A')}")
        print(f"     System Action: {attrs.get('system_action', 'N/A')}")
        print(f"     Examples:")
        for example in attrs.get('examples', [])[:2]:
            print(f"       - {example}")


# For quick testing
if __name__ == "__main__":
    G = build_clinical_graph()
    
    print("=" * 60)
    print("Clinical Knowledge Graph Summary")
    print("=" * 60)
    print(f"Total nodes: {G.number_of_nodes()}")
    print(f"Total edges: {G.number_of_edges()}")
    
    print("\n--- Maladaptive Nodes (Risks) ---")
    for node_id, attrs in get_maladaptive_nodes(G).items():
        print(f"  {node_id}: {attrs['description'][:60]}...")
    
    print("\n--- Adaptive Nodes (Goals) ---")
    for node_id, attrs in get_adaptive_nodes(G).items():
        print(f"  {node_id}: {attrs['description'][:60]}...")
    
    print("\n--- Neutral Nodes (Context) ---")
    for node_id, attrs in get_neutral_nodes(G).items():
        print(f"  {node_id}: {attrs['description'][:60]}...")
    
    print("\n--- Correction Strategies ---")
    for maladaptive in get_maladaptive_nodes(G).keys():
        strategies = get_correction_strategies(G, maladaptive)
        print(f"  {maladaptive} ->")
        for s in strategies:
            print(f"    [{s['priority']}] -> {s['target_node']}")
    
    # Print full references
    print_ontology_with_references()
